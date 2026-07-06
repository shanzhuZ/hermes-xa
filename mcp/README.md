# MCP 目录说明

本目录集中存放 **MCP Server 源码** 与 **基础连接配置**，不含画像路由、Hook、提示词等业务逻辑。

## 目录结构

```
mcp/
├── mcp_servers.yaml      # MCP 连接配置（源文件）
├── install.ps1             # 合并配置到 ../config.yaml
├── servers/                # 自研/拷贝的 Python & Node MCP 实现
│   ├── maigret-mcp-server/
│   ├── ocr-mcp-server/
│   ├── twitter-mcp-server/
│   ├── youtube-mcp-server/
│   ├── bilibili-mcp-server/
│   └── weixin-read-mcp/
└── data/
    └── maigret-reports/    # Maigret 扫描报告输出
```

## 已配置 MCP 列表

| 名称 | 类型 | 说明 |
|------|------|------|
| weibo | pip 可执行文件 | `mcp-server-weibo` |
| weixin_search | pip 可执行文件 | 微信搜一搜 |
| weixin_read | `servers/weixin-read-mcp` | 公众号文章阅读 |
| trends_hub | npm 全局 cmd | 多平台热榜 |
| bilibili | `servers/bilibili-mcp-server` | B站 |
| reddit | npm cmd | Reddit |
| firecrawl | npm cmd | 网页抓取 API |
| playwright | npm cmd | 浏览器自动化 |
| brightdata | HTTP（默认关闭） | 亮数据 |
| apify | npm cmd | Apify Actors |
| maigret | `servers/maigret-mcp-server` | 跨平台用户名扫描 |
| twitter | `servers/twitter-mcp-server` | Twitter/X（仅基础工具） |
| ocr | `servers/ocr-mcp-server` | 本地 Tesseract OCR |
| youtube | `servers/youtube-mcp-server` | YouTube Data API |

## 安装步骤

### 1. Python 包（若未安装）

```powershell
pip install mcp-server-weibo weixin_search_mcp
```

### 2. Node 全局包（若 cmd 不存在）

```powershell
npm install -g mcp-trends-hub @anthropic/mcp-reddit firecrawl-mcp @playwright/mcp @apify/actors-mcp-server
```

（实际包名以本机 `D:\environment\node\node_cache\*.cmd` 为准。）

### 3. 合并 Hermes 配置

```powershell
cd D:\hermes-xa\mcp
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### 4. 环境变量（`D:\hermes-xa\.env`）

| 变量 | 用途 |
|------|------|
| `WEIBO_COOKIE` | 微博 MCP |
| `APIFY_TOKEN` | Apify（Instagram/Telegram/TikTok/Facebook/GitHub） |
| `FIRECRAWL_API_KEY` | Firecrawl（Apify 失败时主页兜底） |
| `YOUTUBE_API_KEY` | YouTube |
| `HTTP_PROXY` / `HTTPS_PROXY` | 需代理的 MCP |
| `BRIGHTDATA_MCP_URL` | Bright Data（可选） |

Twitter Cookie 文件默认：`C:/Users/zhr/.config/twitter-mcp/cookies.json`

**Apify**：`NO_PROXY` 须含 `api.apify.com,apify.com,.apify.com`（与旧 Hermes 一致，避免代理拉 Actor schema 超时）。验证：`hermes mcp test apify` 应显示 9 个工具。

### 5. 验证

```powershell
hermes chat
# 会话内：/reload-mcp
```

## 说明

### MCP 与旧 Hermes 的关系

| 组件 | hermes-xa（本项目） | 旧 Hermes（AppData 等） |
|------|---------------------|-------------------------|
| 启动脚本 | `run_twitter_mcp_data_only.py`（**纯数据，~70 行**） | 可用 `run_twitter_mcp.py` + `TWITTER_PERSONA_TOOLS=1` |
| 源码位置 | `D:/hermes-xa/mcp/servers/`（**独立副本**） | 通常在 `AppData/Local/hermes/...`，**不共用文件** |
| pip 包 `twitter_mcp` | 共享安装，**上游无画像** | 同上 |

- **hermes-xa 已切换** `config.yaml` → `run_twitter_mcp_data_only.py`，不会加载 persona 代码
- **画像扩展**仍在 `run_twitter_mcp.py`，供旧项目保留，勿删
- Maigret/YouTube 画像门禁默认关：`MAIGRET_PERSONA_GATE=0`、`YOUTUBE_PERSONA_TOOLS` 未设

修改 MCP 后：`/reload-mcp`
