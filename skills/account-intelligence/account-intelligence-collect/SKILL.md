---
name: account-intelligence-collect
description: "01采集@种子。Maigret后MCP/Apify只采主页→流比对→相似账号采发文→一次输出三节。禁画像禁web_search。"
version: 1.15.0
author: hermes-xa
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [account-intelligence, collect]
    related_skills: [image-asset-analysis]
---

# 01 · 账号信息采集

**语言**：用户中文输入时，**全程简体中文**（进度、说明、三节报告均不得用英文）。

**数据采集任务**，不是画像。最终只输出三节：`一、个人信息` `二、账号核验依据` `三、发文信息`。

## 启动（只读 1 个参考）

`skill_view` → **`references/collect-rules.yaml`**（唯一参考，禁止再加载其它 references）。

## 铁律

0. **工具名**：步骤 2 **只允许** `mcp_maigret_collect_accounts`；**禁止** `search_username` / `search_usernames` / `mcp_maigret_get_prompt`。Twitter 种子 **必须** `mcp_twitter_get_user_info(screen_name=whyyoutouzhele)`，**禁止** `username` 参数（会报错）。调 MCP 前若不确定参数，先看工具 schema，禁止猜参数名。
0b. **禁止加载** `account-intelligence-profile`（02 画像 Skill）；本任务只做采集，不得 `skill_view` 画像 Skill。
0c. **禁止 clarify**：步骤 1～7 **必须自动跑完**；步骤 3 之后**不得**问用户「是否跳过 4～7」「先做哪个」。无 OCR/vision 时：文本流照常做，图片流只登记 URL，**继续**步骤 5～7。
0d. **单平台硬规则**：若用户明确表示“不要/不需要/仅当前平台/单平台/不跨平台”，则**禁止**调用 `mcp_maigret_collect_accounts`，也**禁止**进入候选主页采集。此时只允许：`1_seed` 种子主页 → 图片/文本分析 → `6_posts` 种子发文 → **`6.5` 图片入库+分析回填** → 三节报告。
1. **步骤 1～6**：禁止 `web_search` / `web_extract` / `browser_*`；禁止写报告、人物传记、综合介绍
2. **步骤 1～6.5**：禁止输出「一、」「二、」「三、」任何内容；最多 2 句进度
3. **Maigret 返回后**：读 `summary.accounts` + `agent_must_do_next`（若有），**禁止**按 MCP 返回写画像
4. **步骤 3 只采主页**：有 MCP→profile；**无原生 MCP→必须 Apify**（不能说「无 MCP 就不采」）；种子 bio 里的 t.me/ 等互链也算候选；失败就跳过
5. **步骤 4** 必须 OCR+vision（有头像）+ 2.1/2.2 表数据；若 `vision_analyze(image_url=远程URL)` 失败，先下载到本地临时文件，再用本地路径重试 1 次
6. **步骤 6** 才对 `validated_accounts` 采发文
6.5. **步骤 6 发文结束后、步骤 7 写报告前**：必须跑图片资产入库 + 分析回填（见下）；失败只记日志/摘要，**禁止**因此把整任务判失败，**禁止**跳过直接写报告
7. **步骤 7** 一次性按 `collect-rules.yaml` 输出三节

## 七步（硬顺序，跨平台任务）

| 步 | 动作 |
|----|------|
| 1 | 种子 MCP profile |
| 2 | Maigret（跨平台时） |
| 3 | Maigret 候选 **+ 种子简介互链**；各条：MCP profile **或** Apify（小 limit）；失败跳过 |
| 4 | 文本流+图片流 vs 种子 |
| 5 | `validated_accounts`（相似账号） |
| 6 | 发文：MCP 或 Apify |
| 6.5 | **图片资产**：发现头像/封面/发文配图 → 下载入库 HBase/`collect_images` → OCR/Vision 回填 |
| 7 | **一次**输出三节 |

## 单平台（用户明确不要跨平台时）

| 步 | 动作 |
|----|------|
| 1 | 种子 MCP profile |
| 2 | 种子文本流 + 图片流分析（仅种子，不做候选比对） |
| 3 | 仅采种子发文 |
| 3.5 | **同 6.5**：图片入库 + 分析回填 |
| 4 | 输出三节 |

单平台任务中：

- 禁止 Maigret
- 禁止步骤 3 候选主页采集
- 禁止生成非种子平台的 `validated_accounts`
- 禁止采集非种子平台发文

## 步骤 6.5 图片入库与分析回填（硬门槛）

发文采集结束后、输出三节报告**之前**，必须用 `terminal` 执行：

1. 取当前 `task_id`（Gateway `X-Hermes-Session-Key: task:{uuid}`，或会话活跃采集任务）。
2. **禁止**用 `search_files` / `web_search`「探测是否部署」；管线就在仓库 `scripts/image_pipeline/`，**视为已部署**。
3. 工作目录切到仓库 `scripts/`（Windows 示例：`cd /d D:\hermes-xa\scripts`），执行（不要 `--skip-analyze`）：

```bash
python -m image_pipeline.run --task-id <taskId> --force-analyze
```

4. 读 stdout JSON；对用户最多 1 句进度（**单独一轮短回复或工具后旁白即可，禁止写进步骤 7 报告**）。命令失败也继续步骤 7，不要编造「未部署 / 未注册」。
5. **task_id**：只用 Gateway `task:{uuid}` 里的 uuid；查不到就跳过 6.5 直接步骤 7，**禁止**在报告里解释原因。
6. **步骤 7 报告必须以 `## 一、个人信息` 开头**，禁止任何脏前缀。

## 步骤 3 工具对照

| 平台 | 工具 |
|------|------|
| YouTube | `mcp_youtube_get_channel_stats(channelId=UC…)` |
| 微博 | `mcp_weibo_get_profile` |
| Instagram | `mcp_apify_apify__instagram_scraper` → run → dataset |
| TikTok | `mcp_apify_clockworks__tiktok_scraper` → run → dataset |
| Telegram | `mcp_apify_vujeen__telegram_channel_scraper`（`channels`: 频道名或 `t.me/xxx`，含种子 bio 互链）→ run → dataset |
| Facebook | `mcp_apify_headlessagent__facebook_profile_post_scraper`（`profileUrls`，步骤3 limit≤5）→ run → dataset |
| GitHub | `mcp_apify_knotless_cadence__github_profile_scraper`（`usernames`，`maxRepos`≤5）→ run → dataset |

**禁止步骤 3**：`get_user_tweets`、`get_user_feeds`、`analyze_channel_videos`、YouTube 搜视频。

## 步骤 7 输出骨架

**硬规则：最终报告正文必须以 `## 一、个人信息` 为第一行**（允许前面仅有空白）。  
**禁止**在第一节之前写任何内容：步骤号、6.5、图片管线、task_id、未注册、继续步骤 7、进度句、「已为 @种子 完成采集」、`---` 分隔线等。

```
## 一、个人信息
【平台·@handle】MCP/Apify 原文字段…

## 二、账号核验依据
### 2.1 文本流（表格）
### 2.2 图片流（表格）
### 2.3 纳入采集的账号
### 2.4 未纳入的候选

## 三、发文信息
【平台·@handle】逐条 MCP/Apify 原文…
```

## 禁止收尾示例

- ❌ 步骤 6.5 / 图片管线 / 未部署 / 未注册 / 继续步骤 7（任何元叙述）
- ❌ 🧾 综合画像报告 / 用户画像总结 / 核心身份 / 发展轨迹
- ❌ 近期推文主题归纳（无原文）
- ❌ 「如果你想进一步了解…」
