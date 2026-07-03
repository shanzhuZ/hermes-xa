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
| `APIFY_TOKEN` | Apify |
| `FIRECRAWL_API_KEY` | Firecrawl |
| `YOUTUBE_API_KEY` | YouTube |
| `HTTP_PROXY` / `HTTPS_PROXY` | 需代理的 MCP |
| `BRIGHTDATA_MCP_URL` | Bright Data（可选） |

Twitter Cookie 文件默认：`C:/Users/zhr/.config/twitter-mcp/cookies.json`

### 5. 验证

```powershell
hermes chat
# 会话内：/reload-mcp
# 尝试：获取微博热搜
```

## 说明

- **Twitter**：`tools.include` 仅保留基础 API 工具，不含 `*_for_persona` 画像专用工具。
- **路径**：所有自研 MCP 已迁到 `D:/hermes-xa/mcp/servers/`，与旧 `AppData/Local/hermes` 解耦。
