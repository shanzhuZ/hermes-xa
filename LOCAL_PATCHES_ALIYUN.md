# 阿里云 Windows 本机补丁记录（防 GitHub 同步覆盖）

> **用途**：从本机 GitHub 拉最新代码后，若下列文件被覆盖，请按本文恢复。  
> **环境**：`D:\hermes-xa`（阿里云 Windows），Clash 代理 `http://127.0.0.1:7897`（`.env` 的 `HTTPS_PROXY`）。  
> **记录日期**：2026-08-11  
> **分支背景**：`fox`

---

## 1. 核心改动：图片 / 视频 VLM 先 Clash 再直连

### 背景

阿里云直连 `api.perplexity.ai` 会 `ConnectTimeout`；走 Clash 可达。  
原代码在图片 VLM 里写死 `proxies={"http": None, "https": None}`，导致入库图 `analyze_status=failed`。

### 策略

1. 读 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` / `HERMES_VIDEO_PROXY`  
2. **先经 Clash 代理 POST**  
3. 仅连接类失败（Proxy/Connect/Timeout/SSL 等）时 **再直连**  
4. 已拿到 HTTP 响应（含 4xx/5xx）不再切换路径  

**不要改**：`scripts/image_pipeline/hbase_store.py` 里对内网 HBase 的 `proxies=None`（故意不走代理）。

---

### 1.1 新增文件（覆盖后若不存在需整文件重建）

#### `scripts/image_pipeline/proxy_http.py`

```python
# -*- coding: utf-8 -*-
"""对外网 LLM/VLM 请求：优先走 Clash/HTTPS_PROXY，失败再直连。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
    "HERMES_VIDEO_PROXY",
)

# 连接类失败才切换路径；HTTP 4xx/5xx 由调用方处理，不在此回退
_RETRYABLE = (
    requests.exceptions.ProxyError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.SSLError,
    requests.exceptions.ReadTimeout,
    requests.exceptions.Timeout,
)


def resolve_clash_proxy_url() -> Optional[str]:
    """读取环境中的代理地址（通常为本地 Clash）。"""
    for key in _PROXY_ENV_KEYS:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def iter_proxy_attempts() -> List[Tuple[str, Dict[str, Optional[str]]]]:
    """[(label, proxies), ...]：有代理则先 clash，再 direct。"""
    attempts: List[Tuple[str, Dict[str, Optional[str]]]] = []
    proxy = resolve_clash_proxy_url()
    if proxy:
        attempts.append(("clash", {"http": proxy, "https": proxy}))
    attempts.append(("direct", {"http": None, "https": None}))
    return attempts


def requests_post_proxy_fallback(
    url: str,
    *,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 120,
    **kwargs: Any,
) -> requests.Response:
    """
    POST：先经 Clash/系统代理，连接失败再直连。
    成功拿到 HTTP 响应（含 4xx/5xx）即返回，不再切换路径。
    """
    last_exc: Optional[BaseException] = None
    attempts = iter_proxy_attempts()
    for i, (label, proxies) in enumerate(attempts):
        try:
            logger.info("HTTP POST via %s", label)
            return requests.post(
                url,
                json=json,
                headers=headers,
                timeout=timeout,
                proxies=proxies,
                **kwargs,
            )
        except _RETRYABLE as exc:
            last_exc = exc
            if i + 1 < len(attempts):
                logger.warning("via %s 失败，改试下一路径: %s", label, exc)
            else:
                logger.warning("via %s 失败且无更多路径: %s", label, exc)
    assert last_exc is not None
    raise last_exc
```

#### `mcp/servers/video2frame-mcp/proxy_http.py`

与上面 **内容相同**（视频 MCP 独立路径，不能依赖 `scripts/image_pipeline`）。

---

### 1.2 修改文件

#### `scripts/image_pipeline/vlm.py`

- **import**：增加  
  `from image_pipeline.proxy_http import requests_post_proxy_fallback`  
  （可去掉仅用于该 POST 的 `import requests`）
- **`analyze_image_bytes` 内 POST**：把原来的  

```python
resp = requests.post(
    api_url,
    json=payload,
    headers=headers,
    timeout=timeout,
    proxies={"http": None, "https": None},
)
```

换成：

```python
# 先 Clash/HTTPS_PROXY，连接失败再直连（阿里云直连 pplx 常超时）
resp = requests_post_proxy_fallback(
    api_url,
    json=payload,
    headers=headers,
    timeout=timeout,
)
```

#### `scripts/report_04/llm_chat.py`

- 去掉顶层 `import requests`（若仅用于该 POST）
- 在 `chat_text` 的 POST 处改为：

```python
# 与图片/视频 VLM 相同：先走 Clash，失败再直连
from image_pipeline.proxy_http import requests_post_proxy_fallback

resp = requests_post_proxy_fallback(
    api_url,
    json=payload,
    headers=headers,
    timeout=timeout,
)
```

（删除原来的 `requests.post(..., proxies={"http": None, "https": None})`）

#### `mcp/servers/video2frame-mcp/analyze.py`

- **import**：  
  `from proxy_http import requests_post_proxy_fallback`  
  （可去掉未再使用的 `import requests`）
- **`_post_chat`** 改为：

```python
def _post_chat(
    *,
    api_url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: int,
):
    """帧分析 / 视频摘要：先 Clash 代理，连接失败再直连。"""
    return requests_post_proxy_fallback(
        api_url,
        json=payload,
        headers=headers,
        timeout=timeout,
    )
```

（原先是 `return requests.post(api_url, json=payload, headers=headers, timeout=timeout)`）

---

### 1.3 恢复后自检

```powershell
# 需 Clash 已开，且 .env 有 HTTPS_PROXY / PERPLEXITY_API_KEY
$env:HERMES_HOME='D:\hermes-xa'
# 加载 HTTPS_PROXY 后：
python -c "import sys,os; sys.path.insert(0,r'D:\hermes-xa\scripts'); from image_pipeline.proxy_http import resolve_clash_proxy_url; print(resolve_clash_proxy_url())"
```

期望打印 `http://127.0.0.1:7897`（或你的代理）。  
若仍 401 quota，是 Perplexity 额度问题，与代理无关。

---

## 2. 其它本机配置改动（同步时注意）

| 项 | 说明 |
|----|------|
| `config.yaml` → `mcp_servers.weixin_search.enabled` | 改为 `false`（本机无 `weixin_search_mcp.exe`，需 Python≥3.12） |
| 系统 PATH | 已加入 `C:\nvm4w\nodejs`（Node MCP `.cmd` 否则找不到 `node`） |
| hermes venv | 已 `ensurepip` + 安装 `mcp==1.28.1`；Gateway 需用 venv 启动 |
| Twitter cookies | `C:\Users\zhr\.config\twitter-mcp\cookies.json`（需含 `auth_token`/`ct0`） |
| Java ↔ Gateway API Key | `.env` 的 `API_SERVER_KEY` 须与 Java `HermesGatewayClient` 硬编码一致 |

这些不一定在仓库文件里；拉代码后若 MCP/Node 又挂了，优先查 PATH 与 `weixin_search`。

---

## 3. 文件清单速查

| 路径 | 操作 |
|------|------|
| `scripts/image_pipeline/proxy_http.py` | **新增**（全文见上） |
| `mcp/servers/video2frame-mcp/proxy_http.py` | **新增**（同上全文） |
| `scripts/image_pipeline/vlm.py` | **改** POST → `requests_post_proxy_fallback` |
| `scripts/report_04/llm_chat.py` | **改** POST → `requests_post_proxy_fallback` |
| `mcp/servers/video2frame-mcp/analyze.py` | **改** `_post_chat` → fallback |
| `config.yaml`（可选） | `weixin_search.enabled: false` |

---

---

## 5. 写报续跑卡死修复 + 视频超时治理 + HBase 入库地址（2026-08-11 续）

### 5.1 背景

两任务卡死共性：**同一 task 内续跑被「在飞/忙」跳过后没有补偿唤醒**。

| 任务 | 现象 |
|------|------|
| `12fcd58e-…` | hold 成功结束，但 posts 因 hold 在飞被跳过，结束后未再催发文 |
| `2a14dcac-…` | posts 成功；analysis 因 YouTube 视频仍 running 被跳过，视频终态后未再催分析 |

另：视频墙钟 600s 内，HBase `192.168.3.171:6666` 不可达时每帧卡 ~30s，拖死分析窗口。

### 5.2 续跑编排（`scripts/report_04/session_continue.py`）

1. **链式续跑**：`run_continue_worker_job` SSE 成功并清 inflight 后，调用 `chain_continue_after_worker`；若 `infer_continue_kind` 为 `posts`/`analysis` 则再催一轮（修 hold→posts 断档）。
2. **延期续跑 deferred**：`maybe_continue` 因「已有续跑在飞 / 发文研判仍 running / osint_done 冷却」跳过时，写入 `step11_report.payload_json`：
   - `flow_continue_deferred=1`
   - `flow_continue_deferred_kind` / `reason` / `skip`
3. **消化 deferred**：worker 收尾 `flush_deferred_continue`；视频终态也会 flush。
4. **hold 不占配额**：`kind==hold` 时不增加 `flow_continue_retries`。
5. 边角：无 session / 已有终稿提前 return 时，在 `finally` 清 deferred，避免永久挂起。

### 5.3 视频终态催分析（`scripts/report_04/video_job.py`）

`complete_post_after_video` 收口父壳后：

- `maybe_continue_agent_session(..., kind="analysis", reason="video_terminal:…")`
- 再 `flush_deferred_continue`

若仍有其它 `step7_video_*` running → skip→deferred，等全部终态再补。

### 5.4 视频时长 / 超时

| 项 | 值 | 文件 |
|----|-----|------|
| 写报视频只分析前 N 秒 | 默认 **120**（`HERMES_REPORT_VIDEO_MAX_DURATION_SEC`） | `video_job.py` / `video_runner.py` |
| 视频 VLM 单请求超时 | 默认 **60s**（`HERMES_VIDEO_VLM_TIMEOUT`） | `mcp/servers/video2frame-mcp/config.py` |
| HBase HTTP 连接超时 | 默认 **3s** | `image_pipeline/config.py` → `connect_timeout_sec` |
| HBase HTTP 总超时 | 默认 **8s**（原 30s） | `HERMES_HBASE_TIMEOUT_MS` |
| HBase 失败冷却 | 默认 **300s** 内后续帧直接本地回退 | `hbase_store.py` `_HTTP_FAIL_*` |

### 5.5 HBase 入库接口地址更换

写入 HTTP 统一改为：

```text
http://47.110.83.229:6666/insertHbaseData
```

已改：

- `.env` / `.env.example` → `HERMES_HBASE_INSERT_URL`
- `scripts/image_pipeline/config.py` 默认值
- `hbase_store.py` 注释、`SKILL.md`、`application.yml` 注释

校验读仍由 insert URL 替换路径为同机 `getHbaseData`。

**注意**：Java 读侧 `HERMES_HBASE_ZK` 若仍指向旧集群，而新 insert 落库不在该 ZK，详情读图会空（本地回退仍可用）。ZK 需与 insert 落库集群对齐后再改。

### 5.6 文件清单

| 路径 | 操作 |
|------|------|
| `scripts/report_04/session_continue.py` | **改** 链式 + deferred + hold 不计次 |
| `scripts/report_04/video_job.py` | **改** 视频终态催 analysis；max_duration 默认 120 |
| `scripts/report_04/video_runner.py` | **改** max_duration 默认 120 |
| `scripts/image_pipeline/hbase_store.py` | **改** 短超时 + 失败冷却；入库 URL 注释 |
| `scripts/image_pipeline/config.py` | **改** HBase 超时默认 + INSERT_URL 默认 |
| `mcp/servers/video2frame-mcp/config.py` | **改** VLM timeout 默认 60 |
| `.env` / `.env.example` | **改** `HERMES_HBASE_INSERT_URL` |

### 5.7 旧卡住任务

代码只影响**新触发的续跑/视频收口**。已卡死任务（如上述两个 id）需手动再催续跑或重跑，不会自动复活。

---

## 6. Tree 接口完成标志（查询逻辑备忘）

入口：`GET /api/tasks/{taskId}/tree`  
实现：`TaskTreeQueryService.buildTaskTree`

### 6.1 数据来源

- `hermes_tasks`：任务 status / phase / session
- `collect_phase_steps`：步骤树（按 `parent_step_key` 组层级）
- `hermes_tool_outputs`：挂到步骤的 `toolNames`（非完成判据）
- 用户输入 + **summary 行**（终稿摘要表）

### 6.2 前端停轮询标志：`pollDone`

响应字段 **`pollDone: true|false`**（不是单靠 `hermes_tasks.status`）。

| 任务类型 | `pollDone == true` 条件 |
|----------|-------------------------|
| **account_report（04 写报）** | ① 有非空 **summary** 内容，且 ② 步骤 **`phase_report` 的 status == `completed`** |
| 其它（01/02/03 等） | 仅需有非空 **summary** |

对应代码注释：

> 停轮询标志：01/02/03 仅需 summary；04 还须 phase_report 已 completed（避免壳未画完就停）

### 6.3 相关但非「整棵树完成」的字段

| 字段 | 含义 |
|------|------|
| `status` | `hermes_tasks.status`（running/completed/failed…），与 `pollDone` 独立 |
| 节点 `status` | 步骤状态；终态集合：`completed` / `skipped` / `failed` |
| 七大壳 `progressPct` | 仅 04 阶段壳按后代进度现算，用于进度条，不单独充当整树完成标志 |
| `content_flag` | 旁白开关，与完成无关 |

### 6.4 04 建议前端判定

```text
pollDone === true
  ⇔ summary 已落库（有正文）
  且 phase_report.status === "completed"
```

任务表 `status=completed` 通常随后也会更新，但 **tree 轮询应以 `pollDone` 为准**。

---

## 7. YouTube Channel not found → 主页子步 skipped（防步骤4卡死）

### 7.1 背景

Agent 口头「失败即跳过」，但 `step4_profile_youtube` 在工具 error 时被故意保持 `running` 等重试；硬失败 `Channel not found`（正式 UC）无意义，导致步骤4/phase 死锁。

### 7.2 改动要点

| 文件 | 行为 |
|------|------|
| `scripts/report_04/sink.py` | `_terminal_profile_failure_reason`：UC/`Channel not found` 等 → `skipped`；工具 error / normalizer 异常后 `_sync_platform_collect_steps(..., tool_output=...)` 并 `close_collect_parent_if_ready`（关父壳会 kickoff 步骤5） |
| `scripts/report_04/step_reconcile.py` | `_skip_failed_only_step4_children` + `maybe_close_abandoned_step4` 早收口；force/quiet 路径累计 `updated_fail` |

### 7.3 旧任务

代码只影响**新工具回调**。已卡在 `running` 的任务需手动 skip/收口后再 `maybe_continue`（例：`545b6c88-…`）。
