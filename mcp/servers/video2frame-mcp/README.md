# video2frame-mcp

独立视频 MCP：直链下载 → 按间隔抽帧 → 多模态分析 → 本地落盘 + MySQL 入库。

**当前未接入 01–04 流程**，仅供独立调试与后续对接。

## 工具

| 工具名 | 用途 |
|--------|------|
| `download_video` | 下载 http(s) 直链到 `data/video_bytes/`，写 `collect_videos` |
| `extract_frames` | 按间隔抽帧（默认每 3 秒），全部落盘 JPG |
| `analyze_frames` | 对帧列表批量 VLM 分析 |
| `run_video_pipeline` | 一键：下载(可选) → 全量抽帧 → 全帧分析 → 整段摘要 → MySQL |

建议每个任务最多处理 **3** 个视频（本阶段仅建议，不硬限制）。

## 存储

- 本地：`{HERMES_HOME}/data/video_bytes/{task_id}/{video_id}/`
  - `source*.mp4`：原视频
  - `frames/*.jpg`：全部抽帧
  - `analysis.json`：结果备份
- MySQL：
  - `collect_videos`：视频元数据 + `video_analysis_text` / `video_analysis_json`
  - `collect_video_frames`：全部帧；约 3 张 `is_preview=1`（约 25%/50%/75%）

## 建表

```bash
mysql -h 127.0.0.1 -u ... -p ... < scripts/sql/013_collect_videos.sql
```

## 依赖

```bash
pip install -r mcp/servers/video2frame-mcp/requirements.txt
```

需本机 OpenCV 可用（`opencv-python` 或 `opencv-python-headless` 二选一；已装其一即可，勿重复安装）。

## 配置

`config.yaml` → `mcp_servers.video2frame`。改完后 `/reload-mcp`。

环境变量见 `.env.example`（默认 VLM 为 Perplexity `sonar`，密钥用 `PERPLEXITY_API_KEY`）。

## 限制（Phase 1）

- 仅支持 **http(s) 直链**（如 `.mp4`），不做 yt-dlp / 平台页解析
- 默认每 **3 秒** 抽一帧，**全部**入库；预览标记约 3 张
