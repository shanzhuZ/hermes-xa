# GitHub 工程封装为 Hermes MCP 指南

> 本文以 **Maigret** 为标杆案例，总结 `hermes-xa` 项目中将第三方 GitHub 工程接入 MCP 的通用做法。  
> 原则：**MCP 只做数据采集与结构化返回；业务流程、报告模板、Agent 约束写在 Skill。**

---

## 1. 总体架构

```
用户 / Agent
    ↓ Hermes tool_call
mcp_<server>_<tool>          ← config.yaml 里 mcp_servers 注册
    ↓ stdio (FastMCP)
mcp/servers/<name>-mcp-server/server.py
    ↓ subprocess / import / HTTP
上游 GitHub 工程（maigret CLI、twitter_mcp pip、YouTube API…）
    ↓
JSON / 文本 返回给 Agent
```

| 层级 | 放什么 | 不放什么 |
|------|--------|----------|
| **MCP Server** | 调上游、超时、代理、解析报告、返回 JSON | 画像报告、六节模板、强制 Agent 话术 |
| **Skill** | 步骤顺序、输出模板、禁止项、何时调哪个 MCP | 直接调 maigret CLI |
| **config.yaml** | command、args、env、timeout、tools.include | 业务逻辑 |
| **.env** | API Key、Cookie、全局代理 | — |

---

## 2. 四种常见封装模式

### 模式 A：CLI 子进程包装（Maigret）

适用：上游是命令行工具，运行时间长，可能卡死 stdio。

```
server.py          → FastMCP 暴露 @mcp.tool()
run_maigret_cli.py → 统一启动 maigret 的参数
maigret_collect_worker.py → 独立子进程跑重任务，结果 JSON 打 stdout
```

要点：

- 主进程 **async**，重活 **subprocess**，避免阻塞 Hermes MCP 管道
- Windows：`stdin=subprocess.DEVNULL`、`encoding=utf-8`、`PYTHONUTF8=1`
- 报告落盘到 `mcp/data/<name>-reports/`，返回里给 `summary`，完整 JSON 路径仅内部用

### 模式 B：pip 包 + 薄启动器（Twitter）

适用：上游已发布 MCP 或 Python 包（如 `twitter_mcp`）。

```
run_twitter_mcp_data_only.py   → 注入代理/Cookie，import 上游 server.main
run_twitter_mcp.py             → 旧项目画像扩展（hermes-xa 不用）
```

要点：

- **data_only 启动器**与**画像扩展**分文件，避免 Agent 误加载 persona 工具
- `config.yaml` → `tools.include` 白名单只留采集工具
- 代理：`TWITTER_PROXY` 或 `${HTTPS_PROXY}`

### 模式 C：pip 包 + 薄启动器（OCR）

适用：上游已发布 MCP 包（如 `mcp-ocr`），仅需配置本机依赖。

```
run_ocr_mcp.py   → 配置 Tesseract 路径后启动上游 mcp-ocr
```

要点：

- **不在 MCP 里写业务逻辑**；账号采集流程由 Skill 约束
- 系统依赖（Tesseract）路径写在 `config.yaml` → `env.TESSERACT_CMD`

### 模式 D：Node/TypeScript 上游 + stdio 适配（YouTube）

适用：上游是 npm 包，默认 HTTP/Smithery，需 stdio。

```
src/stdio-main.ts   → Hermes 专用 stdio 入口
dist/stdio-main.js  → config 里 node 启动
```

要点：

- `npm install && npm run build`
- API Key：`YOUTUBE_API_KEY` in `.env`
- 画像工具用 env 开关（`YOUTUBE_PERSONA_TOOLS`），采集默认关

> **hermes-xa 账号采集**：业务流程、流校验、输出模板均在 `skills/account-intelligence/account-intelligence-collect/`，**不在 MCP 里写死 gate**。

---

## 3. 标准目录结构

在 `mcp/servers/` 下新建：

```
mcp/servers/<project>-mcp-server/
├── server.py              # FastMCP 入口，@mcp.tool() 定义
├── requirements.txt       # Python 依赖（如有）
├── README.md              # 本机依赖、工具列表、运维说明
├── run_*_cli.py           # 可选：CLI 包装
├── *_worker.py            # 可选：子进程 worker
├── test_*.py              # 可选：本地测试
└── tessdata/ / patches/   # 可选：资源文件
```

数据与配置：

```
mcp/
├── mcp_servers.yaml       # MCP 连接配置（源文件）
├── install.ps1            # 合并进 HERMES_HOME/config.yaml
├── data/
│   └── maigret-reports/   # 运行时输出目录
└── README.md
```

---

## 4. 从零到上线：分步清单

### 步骤 1：调研上游

- [ ] GitHub README：安装方式、CLI 参数、输出格式（JSON/ndjson/文本）
- [ ] 许可证、是否需 API Key / Cookie / 代理
- [ ] 单次调用耗时、是否适合在 MCP stdio 里同步跑完
- [ ] 是否已有官方/社区 MCP（有则评估能否直接用或薄包装）

### 步骤 2：划定 MCP 工具边界

每个 `@mcp.tool()` 对应 Agent 的一个**原子能力**，例如：

| 工具名（server 内） | Hermes 侧名称 | 职责 |
|--------------------|---------------|------|
| `collect_accounts` | `mcp_maigret_collect_accounts` | 跨平台用户名扫描 |
| `get_user_info` | `mcp_twitter_get_user_info` | 单账号主页 |

约定：

- 入参：类型明确，`z.string()` / `dict`，写清示例
- 出参：**结构化 dict 或 JSON 字符串**，含 `success`、业务字段、`hint`（给 Agent 的下一步提示，非最终用户文案）
- 失败：返回 `isError` 或 `success: false` + 可读 `error`，不要抛未捕获栈给 Agent

### 步骤 3：实现 server.py

**Python / FastMCP 最小模板：**

```python
#!/usr/bin/env python3
from __future__ import annotations
import os, sys
from mcp.server.fastmcp import FastMCP

# Windows UTF-8（必做）
if sys.platform == "win32":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        if reconf := getattr(stream, "reconfigure", None):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass

mcp = FastMCP("my-project")

@mcp.tool()
async def my_tool(username: str) -> dict:
    """一句话说明工具用途与参数。"""
    # 调上游 CLI / API
    return {"success": True, "data": {...}}

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**环境变量读取模式（与 Maigret 一致）：**

```python
REPORTS_DIR = Path(os.environ.get("MY_REPORTS_DIR", "/tmp/my_reports")).resolve()
DEFAULT_PROXY = os.environ.get("MY_PROXY") or os.environ.get("HTTP_PROXY", "")
DEFAULT_TIMEOUT = int(os.environ.get("MY_DEFAULT_TIMEOUT", "300"))
```

### 步骤 4：子进程与超时（CLI 类上游）

参考 Maigret：

1. FastMCP 工具里组装参数 dict
2. 写入临时 JSON 文件（避免 Windows 命令行转义）
3. `asyncio.create_subprocess_exec(python, worker.py, params.json, env=..., stdin=DEVNULL)`
4. `asyncio.wait_for(proc.communicate(), timeout=...)`
5. stdout 解析 JSON；超时则尝试读部分落盘结果
6. stderr 用 `.decode("utf-8", errors="replace")`

子进程 env 建议：

```python
env = os.environ.copy()
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"
```

### 步骤 5：注册 Hermes 配置

编辑 `mcp/mcp_servers.yaml`，增加：

```yaml
  my_project:
    enabled: true
    command: D:/environment/python/python.exe
    args:
      - D:/hermes-xa/mcp/servers/my-project-mcp-server/server.py
    env:
      FASTMCP_SHOW_SERVER_BANNER: 'false'
      PYTHONUTF8: '1'
      PYTHONIOENCODING: utf-8
      MY_REPORTS_DIR: D:/hermes-xa/mcp/data/my-project-reports
      MY_PROXY: ${HTTPS_PROXY}
    supports_parallel_tool_calls: false   # 长任务建议 false
    timeout: 360
    connect_timeout: 120
```

合并到主配置：

```powershell
cd D:\hermes-xa\mcp
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

或在 `config.yaml` 手工同步 `mcp_servers` 段。

### 步骤 6：环境变量（`.env`）

```env
HTTP_PROXY=http://127.0.0.1:7897
HTTPS_PROXY=http://127.0.0.1:7897
# 内网 LLM/VL 不走代理
NO_PROXY=172.200.200.1,172.200.200.4,localhost,127.0.0.1
# 项目专用
MY_API_KEY=...
```

`${VAR}` 在 `config.yaml` 的 `env` 段由 Hermes 展开。

### 步骤 7：验证

```powershell
# 1. 单独测 MCP（可选）
cd D:\hermes-xa\mcp\servers\my-project-mcp-server
python server.py

# 2. Hermes 内
hermes chat
/reload-mcp
# 让 Agent 调用 mcp_my_project_my_tool
```

检查项：

- [ ] 工具出现在 `tool_search`
- [ ] 参数校验错误信息可读
- [ ] 代理下外网可达、内网在 NO_PROXY 内
- [ ] 无 GBK `UnicodeDecodeError`（子进程 encoding=utf-8）

### 步骤 8：写 Skill / 文档（业务层）

- MCP README：工具表、依赖、env
- Skill：何时调、禁止什么、输出模板
- **不要把**「必须写六节报告」塞进 MCP `hint`（采集模式会误导 Agent）

---

## 5. 标杆案例：Maigret 拆解

| 文件 | 作用 |
|------|------|
| `server.py` | FastMCP；`collect_accounts`、`search_username` 等 |
| `run_maigret_cli.py` | 拼装 maigret 命令行 |
| `maigret_collect_worker.py` | 子进程执行 `_collect_accounts_impl` |
| `requirements.txt` | `maigret`, `mcp`, `curl_cffi` |

**关键 env：**

| 变量 | 含义 |
|------|------|
| `MAIGRET_REPORTS_DIR` | 报告目录 |
| `MAIGRET_PERSONA_GATE=0` | 关闭画像门禁（hermes-xa 默认） |
| `MAIGRET_PROXY` | 扫描用代理 |

账号采集的后续步骤（流校验、发文、输出格式）由 **Skill** 规定，不在 Maigret 返回里写 gate。

**返回体示例：**

```json
{
  "username": "whyyoutouzhele",
  "summary": {
    "found_count": 11,
    "accounts": [{"sitename": "YouTube", "url": "...", "ids": {}}]
  },
  "report_json": "/internal/path.ndjson",
  "hint": "完整 JSON 见 report_json"
}
```

`report_json` 仅供内部；Skill 禁止 Agent 对用户说「已保存 JSON」。

---

## 6. 其它本项目实例对照

| 项目 | 模式 | 上游 | 启动入口 |
|------|------|------|----------|
| Maigret | A CLI+Worker | [soxoj/maigret](https://github.com/soxoj/maigret) | `server.py` |
| Twitter | B 薄启动器 | pip `twitter_mcp` | `run_twitter_mcp_data_only.py` |
| OCR | C 薄启动器 | pip `mcp-ocr` | `run_ocr_mcp.py` |
| YouTube | D Node stdio | [coyaSONG/youtube-mcp-server](https://github.com/coyaSONG/youtube-mcp-server) | `dist/stdio-main.js` |
| Bilibili | 自研 API 客户端 | 公开 API | `server.py` |

---

## 7. Hermes 工具命名规则

配置里 server 名 `maigret` → Agent 调用：

```
mcp_maigret_<tool_name>
```

例如 `collect_accounts` → `mcp_maigret_collect_accounts`。

`tools.include` / `tools.exclude` 可裁剪工具列表（见 `config.yaml` → `mcp_servers.twitter.tools`）。

---

## 8. Windows 专项

| 问题 | 处理 |
|------|------|
| GBK `UnicodeDecodeError` | 进程/env 设 `PYTHONUTF8=1`；subprocess `encoding="utf-8"`；worker stdout 只写 UTF-8 JSON |
| stdio 卡死 | 子进程 `stdin=DEVNULL`；长任务走 worker |
| 路径反斜杠 | YAML 用 `D:/hermes-xa/...` 正斜杠或 `Path.resolve()` |
| 代理 | 外网 MCP 用 `HTTP_PROXY`；内网 VL/LLM 加入 `NO_PROXY` |
| Tesseract/Node | 绝对路径写入 `env`（`TESSERACT_CMD`、`node.exe`） |

---

## 9. 采集类 MCP 的返回体建议

为配合 `account-intelligence-collect` Skill，建议统一字段：

```yaml
success: true
workflow_step: 4          # 可选：门禁 MCP
summary: {}               # 给 Agent 用的精简摘要
hint: ""                  # 下一步动作，非用户可见报告
gate: ""                  # 未完成不得收尾的说明
forbidden_tools: []       # 可选：禁止 web_search 等
```

**反模式：**

- 在 MCP 里写「扫描完成，以下是完整报告」
- 在 MCP 里嵌六节画像模板、`mandatory_output_contract`
- 用 `web_search` 补全 MCP 失败（应在 Skill 禁止，由 discovery_only 处理）

---

## 10. 新增一个 GitHub 工程的快速 Checklist

```
□ 1. fork/拷贝到 mcp/servers/<name>-mcp-server/
□ 2. pip install -r requirements.txt / npm install && build
□ 3. server.py + FastMCP @mcp.tool
□ 4. 长任务 → worker 子进程 + 超时 + 部分结果恢复
□ 5. Windows UTF-8 三板斧
□ 6. mcp_servers.yaml 增加段 + install.ps1
□ 7. .env 补 Key / Proxy / NO_PROXY
□ 8. /reload-mcp 冒烟
□ 9. README.md（工具表 + env）
□ 10. Skill 引用（只写调用顺序，不写进 MCP）
```

---

## 11. 相关路径

| 路径 | 说明 |
|------|------|
| `mcp/README.md` | MCP 目录总览 |
| `mcp/mcp_servers.yaml` | 连接配置源 |
| `mcp/install.ps1` | 合并进 `config.yaml` |
| `config.yaml` → `mcp_servers` | Hermes 运行时配置 |
| `docs/workflows/01-账号信息采集.md` | 业务工作流 |
| `skills/account-intelligence/account-intelligence-collect/` | 采集 Skill |

---

## 12. 总结一句话

**GitHub 工程进 hermes-xa 的标准路径：在 `mcp/servers/` 用 FastMCP 包一层 → 处理好 CLI/代理/UTF-8/超时 → 注册 `mcp_servers.yaml` → **业务流程写在 Skill**，MCP 只返回结构化数据。

Maigret 是 **CLI + Worker + 报告解析** 的完整样板；Twitter/OCR/YouTube 则是同一原则下的变体。
