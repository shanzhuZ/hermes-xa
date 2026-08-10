# 阿里云 Windows 服务器部署指南（给 Cursor Agent 执行）

> **读者**：新服务器上已安装 Cursor 的 Agent。  
> **目标**：在阿里云 Windows Server 上把 `hermes-xa` 从零部署到可跑 `hermes chat` / `hermes gateway`。  
> **原则**：逐步执行、每步验证；缺密钥/内网地址时停下来问用户，不要伪造 Key、不要提交 `.env`。  
> **参考**（细节冲突时以本文件 + 用户当场答复为准）：  
> - [协作者本地环境搭建指南.md](./协作者本地环境搭建指南.md)  
> - [笔记本迁移与对齐指南.md](./笔记本迁移与对齐指南.md)

---

## 0. 执行前先问用户（缺一不可）

在动手改配置前，向用户确认并记录：

| 项 | 说明 | 示例 |
|----|------|------|
| 项目根目录 | 建议固定 | `D:\hermes-xa` |
| Git 分支 | **必须用 `fox`**，不要用 `main` | `fox` |
| 仓库可见性 | 私有仓需 Token / SSH | HTTPS + PAT 或 SSH key |
| 密钥来源 | `.env` / `auth.json` / Cookie **不在 Git** | 用户用 RDP/WinSCP 从开发机拷贝 |
| MySQL | 本机新建 / 连现网 / 暂不入库 | `HERMES_PERSIST_ENABLED=0` 可先跑通对话 |
| 模型与代理 | DeepSeek/Grok/内网 LLM；是否装 Clash | 影响 `NO_PROXY` / MCP 外网 |
| HBase / ES | 云上是否可达原内网地址 | 不可达则关闭或改地址 |
| Java 前端对接 | 是否需要 `clients/hermes-xa` + gateway | 仅对话可不装 Java |

**默认约定（用户未另说明时采用）：**

- 路径：`D:\hermes-xa`
- 分支：`fox`
- `HERMES_HOME=D:\hermes-xa`
- 先保证对话 + MCP；入库/HBase/ES 按用户提供的地址再开

---

## 1. 系统前置（Agent 用 PowerShell 检查）

```powershell
# 以管理员 PowerShell 更稳（装软件、写系统环境变量时）
$ErrorActionPreference = "Continue"

Write-Host "=== OS ==="
[System.Environment]::OSVersion.VersionString
$env:COMPUTERNAME

Write-Host "=== Tools ==="
git --version
python --version
node --version
npm --version
where.exe git
where.exe python
where.exe node
```

### 1.1 缺失则安装

优先 `winget`（无 winget 则让用户从官网装）：

```powershell
winget install --id Git.Git -e --accept-source-agreements --accept-package-agreements
winget install --id Python.Python.3.11 -e --accept-source-agreements --accept-package-agreements
winget install --id OpenJS.NodeJS.LTS -e --accept-source-agreements --accept-package-agreements
winget install --id UB-Mannheim.TesseractOCR -e --accept-source-agreements --accept-package-agreements
```

安装后 **新开终端**，再确认：

- Python **3.10+**（推荐 3.11）
- Node **18+**
- Git 可用

可选（要跑 Java API 时再装）：JDK 17+、Maven 3.8+。

---

## 2. Git 克隆业务仓库

```powershell
# 目标盘有空间即可；建议 D:
if (-not (Test-Path "D:\")) { Write-Host "无 D: 盘，请改路径并全程替换下文 D:\hermes-xa" }

# 若目录已存在且非空，先问用户：覆盖 / 另开目录 / git pull
git clone -b fox https://github.com/shanzhuZ/hermes-xa.git D:\hermes-xa
cd D:\hermes-xa

git branch --show-current    # 必须 fox
git log -1 --oneline
git status --short
git remote -v
```

### 2.1 私有仓认证失败时

- HTTPS：让用户提供 GitHub PAT，用  
  `git clone -b fox https://<TOKEN>@github.com/shanzhuZ/hermes-xa.git D:\hermes-xa`  
  **不要把 Token 写进文档或提交到仓库**
- 或配置 SSH key 后改用 `git@github.com:shanzhuZ/hermes-xa.git`

### 2.2 禁止事项

| 不要做 | 原因 |
|--------|------|
| 从开发机整包拷 `venv/`、`node_modules/`、`hermes-agent/venv/` | 跨机必坏 |
| 拷 Cursor `state.vscdb` | 会导致 Chat 卡死 |
| 使用 `main` 分支当生产 | `main` 缺完整 01～04 业务（以仓库实际为准，当前以 **fox** 为准） |
| 把填好的 `.env` / Cookie 提交 Git | 泄密 |

---

## 3. 环境变量（关键）

### 3.1 `HERMES_HOME`（必须）

Hermes 读的是 **`%HERMES_HOME%\config.yaml`**。不设会落到 `%LOCALAPPDATA%\hermes`，表现为「配置改了不生效」。

```powershell
cd D:\hermes-xa

# 当前进程
$env:HERMES_HOME = "D:\hermes-xa"

# 用户级永久（推荐）
[System.Environment]::SetEnvironmentVariable("HERMES_HOME", "D:\hermes-xa", "User")

# 可选：固定本机 Python，供 hooks 同步脚本使用（正斜杠）
$py = (Get-Command python).Source
$pyUnix = $py -replace '\\','/'
[System.Environment]::SetEnvironmentVariable("HERMES_PYTHON", $pyUnix, "User")
$env:HERMES_PYTHON = $pyUnix

Write-Host "HERMES_HOME=$env:HERMES_HOME"
Write-Host "HERMES_PYTHON=$env:HERMES_PYTHON"
```

**验证**：新开 PowerShell 后 `echo $env:HERMES_HOME` 仍为 `D:\hermes-xa`。

### 3.2 可选系统级 PATH

若 `hermes` 装完仍找不到命令，把安装器提示的 Scripts 目录加入用户 PATH，并重启终端。

### 3.3 Windows 编码（减少 hook / MCP 乱码）

```powershell
[System.Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
$env:PYTHONUTF8 = "1"
```

---

## 4. 安装 Hermes Agent 核心（不在 Git 内）

```powershell
cd D:\hermes-xa
$env:HERMES_HOME = "D:\hermes-xa"
.\install.ps1 -NonInteractive -SkipSetup
```

验证：

```powershell
hermes --version
# 确认存在目录：
Test-Path D:\hermes-xa\hermes-agent
```

若 `hermes` 找不到：关闭并重开终端，或执行安装器输出的 PATH 提示后再试。

**不要**指望从开发机拷浅克隆的 `hermes-agent` 当长期方案；云上用 `install.ps1` 重装最稳。

---

## 5. 密钥与本地机密文件（用户手工提供）

> Agent **不能**从 Git 得到这些文件。请用户用 RDP / WinSCP 从开发机拷贝。

### 5.1 必拷 / 必建

| 文件 | 说明 |
|------|------|
| `D:\hermes-xa\.env` | 优先从开发机拷贝整份；没有则 `copy .env.example .env` 再填 |
| `D:\hermes-xa\auth.json`（若用 xAI OAuth / Grok） | 从开发机拷贝 |
| Twitter Cookie JSON | 见 `mcp/mcp_servers.yaml` 中 `TWITTER_COOKIES` 路径 |

### 5.2 `.env` 云上必改项（Agent 打开核对）

```powershell
cd D:\hermes-xa
if (-not (Test-Path .env)) { copy .env.example .env }
```

至少核对：

```env
# 模型 Key（与 config.yaml 的 model.provider 一致）
DEEPSEEK_API_KEY=...
# 或 OPENAI_API_KEY / 其它

# 外网代理（服务器上 Clash 端口按实际改；没有代理则注释掉并保证 MCP 能直连）
HTTP_PROXY=http://127.0.0.1:7897
HTTPS_PROXY=http://127.0.0.1:7897

# 内网/直连必须进 NO_PROXY（按实际地址追加）
NO_PROXY=localhost,127.0.0.1,api.apify.com,apify.com,.apify.com

# MySQL
HERMES_DB_HOST=127.0.0.1
HERMES_DB_PORT=3306
HERMES_DB_USER=root
HERMES_DB_PASSWORD=...
HERMES_DB_NAME=hermes-xa
HERMES_PERSIST_ENABLED=1

# 图片 HBase（云上连不到原内网则先关）
HERMES_HBASE_ENABLED=0
# HERMES_HBASE_INSERT_URL=...
# HERMES_HBASE_ZK=...

# Gateway（Java 对接时）
# API_SERVER_ENABLED=true
# API_SERVER_HOST=127.0.0.1
# API_SERVER_PORT=8642
# API_SERVER_KEY=至少16位随机串
```

**规则：**

- 原开发机 `172.x` / `192.168.x` 在云上不可达 → 改成可达地址，或关闭对应能力  
- Apify：`NO_PROXY` 必须含 `api.apify.com,apify.com,.apify.com`  
- 若启用 vision 内网地址，同样加入 `NO_PROXY`

---

## 6. 改绝对路径（最容易漏）

仓库里的 `config.yaml`、`mcp/mcp_servers.yaml` 含开发机路径，**必须替换**。

### 6.1 探测本机路径

```powershell
where.exe python
where.exe node
where.exe tesseract
$py = (Get-Command python).Source -replace '\\','/'
$node = (Get-Command node).Source -replace '\\','/'
Write-Host "PYTHON=$py"
Write-Host "NODE=$node"
```

### 6.2 全局替换映射（按探测结果改「新」列）

在 `mcp/mcp_servers.yaml` 与 `config.yaml` 中替换：

| 旧（开发机常见） | 新（本机） |
|------------------|------------|
| `D:/environment/python/python.exe` | 上一步 `PYTHON=` |
| `D:/environment/python/Scripts/` | 本机 Scripts 目录（`python -c "import sysconfig; print(sysconfig.get_path('scripts'))"`） |
| `D:/environment/node/node_cache/` | 本机 npm 全局 prefix（见下） |
| `C:/nvm4w/nodejs/node.exe` | 上一步 `NODE=` |
| `D:/hermes-xa` | `D:/hermes-xa`（根目录不同则全替换） |
| `C:/Users/zhr/.config/twitter-mcp/cookies.json` | 本机实际 Cookie 路径 |
| `C:/Program Files/Tesseract-OCR/tesseract.exe` | `where tesseract` 结果 |

查 npm 全局目录：

```powershell
npm root -g
npm config get prefix
```

路径在 YAML 里统一用 **正斜杠** `D:/hermes-xa/...`。

### 6.3 hooks（db_sink）必须指向本机

`config.yaml` 中类似：

```yaml
hooks:
  pre_llm_call:
    - command: <本机python> D:/hermes-xa/scripts/db_sink.py
      timeout: 120
  # post_tool_call / pre_tool_call / pre_verify 等同样改
```

可用仓库脚本同步（需已设 `HERMES_PYTHON`）：

```powershell
cd D:\hermes-xa
$env:HERMES_HOME = "D:\hermes-xa"
$env:HERMES_PYTHON = ((Get-Command python).Source -replace '\\','/')
python scripts\sync_collect_hooks.py
```

### 6.4 合并 MCP 到 config.yaml

```powershell
cd D:\hermes-xa\mcp
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

---

## 7. 安装依赖

### 7.1 Python（入库 + MCP）

```powershell
cd D:\hermes-xa
python -m pip install -U pip
python -m pip install -r scripts\requirements-persist.txt
python -m pip install mcp maigret curl_cffi mcp-ocr pytesseract twitter_mcp mcp-server-weibo weixin_search_mcp
python -m pip install -r mcp\servers\maigret-mcp-server\requirements.txt
python -m pip install -r mcp\servers\bilibili-mcp-server\requirements.txt
```

若还有 `video2frame-mcp` / 其它 server 的 `requirements.txt`，按 `mcp/mcp_servers.yaml` 实际启用项再装。

### 7.2 YouTube MCP（必须 build）

```powershell
cd D:\hermes-xa\mcp\servers\youtube-mcp-server
npm install
npm run build
Test-Path Dist\stdio-main.js   # 或 dist\stdio-main.js，以实际为准
cd D:\hermes-xa
```

确认 `mcp/servers/youtube-mcp-server/dist/stdio-main.js` 存在。

### 7.3 Node 全局 MCP（与 yaml 中 command 对齐）

```powershell
npm install -g @apify/actors-mcp-server @playwright/mcp firecrawl-mcp reddit-mcp-buddy mcp-trends-hub
```

装完后把 `mcp_servers.yaml` 里的 `.cmd` 路径改成 `npm root -g` / `npm prefix -g` 下的真实路径，再执行一次 `mcp\install.ps1`。

### 7.4 OCR 语言包

- 系统 Tesseract 已装  
- 确认 `mcp/servers/ocr-mcp-server/tessdata/chi_sim.traineddata` 存在（缺失则从开发机拷贝 tessdata 目录）  
- `TESSDATA_PREFIX` 指向该 tessdata 目录

---

## 8. MySQL / 外围存储（按需）

### 8.1 仅验证对话

```env
HERMES_PERSIST_ENABLED=0
```

### 8.2 需要入库

1. 安装 MySQL 8，或连通现网库（安全组勿对公网裸奔 3306）  
2. 建库：`hermes-xa`（字符集 utf8mb4）  
3. 表结构按团队既有库导出导入，或问用户要 dump  
4. 验证：

```powershell
cd D:\hermes-xa\scripts
python -c "from collect_01 import db; print(db.fetch_one('SELECT 1 AS ok'))"
```

### 8.3 HBase / ES

云主机访问不到原 `192.168.x` 时：

- `HERMES_HBASE_ENABLED=0`  
- ES MCP 先禁用或改 `ES_HOST`  
不要硬连超时地址拖死任务。

---

## 9. 验证（按顺序）

### 9.1 基础

```powershell
cd D:\hermes-xa
$env:HERMES_HOME = "D:\hermes-xa"
hermes --version
Test-Path .\config.yaml
Test-Path .\skills\account-intelligence\account-intelligence-collect
Test-Path .\skills\account-intelligence\account-intelligence-report
```

### 9.2 MCP

```powershell
hermes mcp list
hermes mcp test twitter
hermes mcp test maigret
hermes mcp test youtube
hermes mcp test ocr
hermes mcp test apify
```

失败时逐个查：路径 / Key / 代理 / 是否 `npm run build`。

### 9.3 对话烟测

```powershell
cd D:\hermes-xa
hermes chat
```

会话内：

```text
/reload-mcp
/tools
```

短问一句确认模型 Key 可用。

### 9.4 Gateway（Java / 任务 API 需要时）

```powershell
cd D:\hermes-xa
# 确认 .env 中 API_SERVER_* 已配置
hermes gateway
```

另开终端检查端口（默认 8642）监听正常。改 `scripts/` 或 hooks 后需 **重启 gateway**。

### 9.5 业务 sink 可导入

```powershell
cd D:\hermes-xa\scripts
python -c "from report_04.sink import handle_event; from verify_03.sink import handle_event; print('sink import OK')"
```

### 9.6（可选）Java

```powershell
cd D:\hermes-xa\clients\hermes-xa
mvn clean package -DskipTests
```

`application.yml` 里 gateway 地址指向本机 `8642`（或实际端口）。

---

## 10. 日常更新

```powershell
cd D:\hermes-xa
$env:HERMES_HOME = "D:\hermes-xa"
git pull origin fox
```

| 变更 | 动作 |
|------|------|
| 仅 `skills/**` | 新会话或重新 `/skill` |
| `mcp/mcp_servers.yaml` | 改路径 → `mcp\install.ps1` → `/reload-mcp` |
| `mcp/servers/**` Python | `pip install -r ...` → `/reload-mcp` |
| YouTube 源码 | `npm run build` → `/reload-mcp` |
| `scripts/**` / hooks | 重启 `hermes gateway` |
| `.env.example` 新增键 | 合并进本地 `.env`，勿覆盖已有密钥 |

---

## 11. 完整检查清单

```
□ Windows 已装 Git / Python3.11 / Node18+ /（可选）Tesseract
□ git clone -b fox → D:\hermes-xa，branch=fox
□ HERMES_HOME=D:\hermes-xa（用户环境变量已设，新终端仍有效）
□ HERMES_PYTHON 指向本机 python（正斜杠）
□ .\install.ps1 完成，hermes --version 可用
□ 用户已提供 .env（及 auth.json / Twitter Cookie）
□ .env 中代理、NO_PROXY、DB、HBase 已按云上可达性改过
□ mcp_servers.yaml + config.yaml 绝对路径已替换
□ sync_collect_hooks.py 或手工 hooks 指向本机 python + db_sink.py
□ mcp\install.ps1 已执行
□ pip：requirements-persist + MCP 依赖已装
□ YouTube：npm install && npm run build，dist 存在
□ npm -g 全局 MCP 已装且路径对齐
□ hermes mcp test 关键项 Connected（按启用项）
□ hermes chat 能对话；/reload-mcp 正常
□ （可选）MySQL SELECT 1 成功
□ （可选）hermes gateway 监听 8642
□ （可选）report_04 / verify_03 sink import OK
□ 未把 .env / Cookie / auth.json 提交到 Git
```

---

## 12. 常见问题（Agent 排障顺序）

| 现象 | 先查 |
|------|------|
| 配置改了不生效 | `echo $env:HERMES_HOME`；是否读了 `%LOCALAPPDATA%\hermes` |
| MCP 全挂 | `config.yaml` 是否仍是 `D:/environment/...`；是否跑过 `mcp\install.ps1` |
| YouTube 挂 | 缺 `dist/stdio-main.js` |
| vision / 内网超时 | `NO_PROXY` 是否包含目标 IP/域名；是否误走 HTTP_PROXY |
| Apify TLS/超时 | `APIFY_TOKEN`；`NO_PROXY` 含 apify 域名 |
| hook 不入库 | `HERMES_PERSIST_ENABLED`；hooks 的 python 路径；Windows 编码（`PYTHONUTF8=1`）；看 `logs/*sink*` |
| `hermes` 不是命令 | 重开终端；检查 PATH；重跑 `install.ps1` |
| Skill 名找不到 | 04 写报 Skill 目录名是 `account-intelligence-report`，不是 `account-intelligence` |
| 安全组 | RDP 3389 限源 IP；MySQL/Gateway 不要对 `0.0.0.0/0` 裸奔 |

---

## 13. 给 Cursor Agent 的工作方式

1. **打开本仓库根目录** `D:\hermes-xa` 作为工作区。  
2. **按本文第 0 节向用户确认** 密钥与网络，再执行第 1～9 节。  
3. 每完成一大步输出：做了什么、验证命令、结果（成功/失败摘要）。  
4. 失败时贴关键报错前/后各约 20 行，不要一次改十处配置。  
5. 需要开发机文件时，明确列出路径让用户 RDP/WinSCP 拷贝，**不要编造密钥**。  
6. 部署完成后，用第 11 节清单逐项打勾回复用户。

---

## 14. 用户侧准备清单（可直接转发给用户）

请用户在开 Cursor 部署对话前准备好：

1. 阿里云 Windows 已能 RDP 登录；已装 Cursor  
2. 开发机上的：`D:\hermes-xa\.env`、`auth.json`（如有）、Twitter cookies  
3. GitHub 访问方式（公开 / PAT / SSH）  
4. 云上是否装代理、MySQL 用本机还是远程  
5. 是否需要 Java + `hermes gateway` 对外（若对外，安全组只放行必要端口）

用户在新服务器 Cursor 中开新对话，附上：

```text
请严格按 @docs/阿里云Windows服务器部署指南-给Cursor执行.md 从零部署 hermes-xa。
项目路径 D:\hermes-xa，分支 fox。密钥文件我会用 RDP 拷贝。
```
