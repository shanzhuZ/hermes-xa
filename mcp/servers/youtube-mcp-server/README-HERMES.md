# YouTube MCP（Hermes 接入说明）

上游：[coyaSONG/youtube-mcp-server](https://github.com/coyaSONG/youtube-mcp-server)

上游主分支已改为 Smithery HTTP；本目录增加 `src/stdio-main.ts`，供 Hermes **stdio** 托管。

## 前置

1. [Google Cloud](https://console.cloud.google.com/) 启用 **YouTube Data API v3**，创建 API Key
2. 复制 `.env.example` 为 `.env`，填写 `YOUTUBE_API_KEY`

## 安装与构建

```powershell
cd C:\Users\zhr\AppData\Local\hermes\hermes-mcp\youtube-mcp-server
npm install
npm run build
```

## Hermes 配置

`config.yaml` → `mcp_servers.youtube`（已写入）。环境变量可在 `config.yaml` 的 `env` 段直接写 Key，或使用 `${YOUTUBE_API_KEY}` 从进程环境读取。

改配置后：**重启 Dashboard → `/reload-mcp`**

## 画像专用工具

**第一轮只调** `get-channel-for-persona`（Hermes 名：`mcp_youtube_get_channel_for_persona`）。

返回体含 `compliance_gate`、`mandatory_output_contract`、`section2_citation_template`（与 Twitter persona 对齐）。

## 其它工具

- `search-videos` — 搜索视频
- `get-video-transcript` / `enhanced-transcript` — 字幕
- `get-video-stats` / `get-channel-stats` — 统计；**`channelId` 可传 UC… 或账号名/@handle/频道 URL（服务端自动解析）**
- `analyze-channel-videos` — 频道近期视频；**同样支持 handle 自动解析**
- `get-trending-videos` — 趋势
- 等（见上游 README）

改代码后请 `npm run build`，并 **重启 Hermes / `/reload-mcp`** 使新 dist 生效。
