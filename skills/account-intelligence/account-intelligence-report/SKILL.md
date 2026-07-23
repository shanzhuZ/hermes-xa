---
name: account-intelligence-report
description: "04写报@种子。步骤2仅mcp_maigret_collect_accounts→步骤3网页检索→主页/流/发文→7.5图片入库→分析→画像报告。禁search_username。"
version: 1.21.0
author: hermes-xa
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [account-intelligence, report]
    related_skills: [image-asset-analysis]
---

# 01 · 账号画像报告
**任务**：输入种子账号→全网搜索关联账号→核查关联账号与种子账号的关联性→采集种子账号与关联账号的全部发文内容→结合发文与配图分析账号信息→形成账号画像

## 启动（只读 1 个参考）
`skill_view` → **`references/collect-rules.yaml`**（唯一参考，禁止再加载其它 references）。

## 铁律
- **全程使用中文简体输出呈现**
- **执行流程中的每一步都必须要执行，未满足执行条件说明原因！**
- **执行流程必须严格呈现出 “步骤X： xxxxx”**
- **步骤 2 Maigret 工具（硬约束）**：**只允许** `mcp_maigret_collect_accounts(username=种子handle)`；**禁止** `mcp_maigret_search_username`、`mcp_maigret_search_usernames`、`mcp_maigret_get_prompt`、`search_username`、`search_usernames`。调错工具会导致步骤二无法收口、步骤三被门禁挡住。
- **仅步骤 3 允许** `web_search` / `web_extract` / `browser_*`；**其余所有步骤（1、2、4～11）禁止**这三类工具（**唯一例外**：步骤4 的 YouTube 子步骤仍 pending/running 且仅有 `@handle` 时，可用 `web_search`/`web_extract` 解析出正式 `UC…` channelId）；全程禁止写报告、人物传记、综合介绍（步骤11 的最终画像报告除外）
- **步骤 1、2、4～11**：详细描述执行流程
- **Maigret 返回后**：读 `summary.accounts` + `agent_must_do_next`（若有），**禁止**按 MCP 返回写画像。Maigret 常只给 YouTube `@handle`/链接，**不等于**可调 MCP；**必须在步骤3（或步骤4例外 web）解析出 `UC…`** 才能采 YouTube 主页
- **步骤3用浏览器 + 搜索引擎** 搜索类似昵称的账号及账号ID， 严查推特（X）、facebook、telegram、youtube、github、reddit、weibo、linkedin、ins、vk等中大型社交网站；**YouTube 候选必须落到 `channelId=UC…`**（禁止把 `@handle` 当 channelId）
- **步骤 2 与步骤 3 的候选合并去重**：Maigret 候选 + web_search 候选按 平台+handle 去重，形成统一候选列表供步骤4遍历
- **步骤 4 只采主页**：有 MCP→profile；无 MCP→Apify；**失败就跳过**，不换工具；**禁止**因「其它平台已采完」提前跳过尚未轮到的平台（含 youtube/github）；**全部 step4_profile_* 子节点终态前禁止 vision/OCR**
- **步骤 5** 批次闭环：步骤4全部主页子节点终态后，对本批入库头像 **一次列齐并全部 vision**（可并行）；OCR 可选；远程 URL 失败即该流终态。**全部图片流终态后系统自动收口步骤5并进入步骤6；收口后禁止再调 vision/OCR**（勿回补）。全部图片流结束前禁止任何发文工具
- **步骤 6** 由系统收敛 `validated_accounts`（勿空转宣称完成）；完成前 **禁止**发文工具与 Apify 发文轮
- **步骤 7** 才对 `validated_accounts` 采发文，发文采集范围最近90天；**种子平台必须单独采发文**（Twitter/YouTube/微博用 MCP 发文工具；Instagram/TikTok/Telegram/Facebook/GitHub 再走一轮 Apify→dataset 入库 posts）。**禁止**因步骤1已采过主页而跳过种子平台发文；步骤1 Apify 只保留 profile，不算步骤7发文
- **步骤 7.5（硬门槛）**：步骤7发文全部结束后、步骤8之前，必须跑图片资产入库+分析回填（见下）；失败只记日志/摘要，**禁止**因此把整任务判失败，**禁止**跳过直接写步骤8～11
- **硬顺序**：步骤5 → 步骤6 → 步骤7 → **7.5 图片资产** → 步骤8/9/10，不可并行抢跑；步骤5未完成时调用发文工具会被系统拦截并要求继续 vision
- **步骤3→4**：web_search 未停轮前不要宣称步骤3完成；步骤4主页工具开始后禁止再 web_search（YouTube 解析 UC 例外见上）
- **步骤5→6→7**：图片流批次收口后进步骤6；步骤7仅在真正调用发文工具时开始，禁止「vision 还在跑、步骤7已 running」
- **步骤 8、步骤9、步骤10**：在同一次响应内同时发起（并行）；三步分析结果必须完全展示呈现；步骤8可优先结合已入库的 `collect_images` / 图片 API，勿再全量空跑未入库 CDN
- **步骤11** 结合 步骤9、步骤10的分析结果为数据基础。
- **步骤11** 必须按照整体章节的结构输出， 每一章节内容必须使用整段叙述性文字描述。不要换行输出展示


## 执行流程（硬顺序，不可省略任意一步）
| 步骤 | 动作                                                                                 |
|----|------------------------------------------------------------------------------------|
| 1  | 种子 MCP/Apify profile（Instagram/TikTok/Telegram/Facebook/GitHub 走 Apify 三轮） |
| 2  | **`mcp_maigret_collect_accounts(username=种子)`** 跨平台发现候选；**禁止** search_username 等其它 Maigret 工具 |
| 3  | web_search, web_extract, browser_* 检索种子账号昵称及账号id获取候选社交账号                         |
| 4  | 各候选主页：MCP profile **或** Apify；失败跳过                                        |
| 5  | 文本流+图片流 vs 种子                                                                      |
| 6  | `validated_accounts`（相似账号）                                                         |
| 7  | 发文：MCP 或 Apify                                                                     |
| 7.5 | **图片资产**：发现头像/封面/发文配图 → 下载入库 HBase/`collect_images` → OCR/Vision 回填 |
| 8  | 图片流： 分析账号头像、账号背景图片、账号发文配图的关联信息                                                     |
| 9  | 文本流： 结合 各个平台的账号发文 分析发文观点及涉华发言 并配有发文作为佐证                                            |
| 10 | 文本流： 结合 各个平台的账号发文 分析真实姓名、年龄、籍贯、常住地、活动城市、生活习惯、教育经历、工作经历、对华态度、电话邮箱码值、社交、亲友、同事等三个圈层关系 |
| 11 | 一次输出账号画像报告：一、账号基本信息 二、账号全网关联账号 三、账号网络活动情况 四、核查思路                                   |

## 步骤 1 种子主页（MCP 或 Apify）

| 平台 | 工具 |
|------|------|
| Twitter/微博/YouTube/B站 | 对应 MCP profile |
| Instagram/TikTok/Telegram/Facebook/GitHub | Apify Actor → `get_actor_run` → `get_dataset_items`（只取主页） |

**步骤1禁止**：发文工具、Maigret、OCR/vision、web_search。

## 步骤 5 图片流（Vision 批次闭环）

| 规则 | 说明 |
|------|------|
| 开门 | 仅当步骤4全部 `step4_profile_*` 终态 |
| URL | 只用已入库的 `avatar_url` / 图片流 `payload_url`；一次列齐本批全部待 vision |
| OCR | 人脸无字默认跳过；失败或 No text → 立刻 vision，禁止同参重试 |
| Vision | **同一 LLM 回合并行齐发** N 次 `vision_analyze`（N=待处理条数）；每条 1 次；禁止每 5 分钟只跑 1 张 |
| 禁止 | 步骤5收口后再调 vision；禁止对未入库 CDN 空跑；禁止未齐就写步骤6/发文 |

## 步骤 7.5 图片入库与分析回填（硬门槛）

步骤7发文工具全部结束后、进入步骤8之前必须执行：

1. 取得当前写报 `task_id`（Gateway `task:{uuid}` 或会话活跃任务）。
2. **禁止**用 `search_files` / `web_search`「探测是否部署」；管线在仓库 `scripts/image_pipeline/`，**视为已部署**。
3. 工作目录在 `scripts/`（或 `PYTHONPATH` 含 `scripts`），执行：

```bash
python -m image_pipeline.run --task-id <taskId> --force-analyze
```

4. 将 stdout JSON 摘要最多用 1 句进度说明；失败只记日志，**禁止**把整任务判失败，**禁止**跳过直接写步骤8～11。
5. 步骤树**不新增**节点；系统 Hook 会在 7→8 / finalize 漏跑时兜底，但 **Agent 仍须主动执行本命令**。

**禁止在报告正文写**：步骤 7.5、图片管线、未部署、task_id、Hook 等元叙述。
**禁止**因终端报错/找不到会话就自编「管线未找到 / 即席执行 / 跳过步骤 7.5」写进 stream 或终稿前缀；图片入库由系统 Hook 兜底。

## 步骤 2 Maigret（必须用 collect_accounts）

| 允许 | 禁止 |
|------|------|
| `mcp_maigret_collect_accounts(username=<种子handle>)` | `mcp_maigret_search_username` |
| 读返回的 `summary.accounts` + `agent_must_do_next`（若有） | `mcp_maigret_search_usernames` |
| 步骤二完成后再进入步骤三 | `mcp_maigret_get_prompt`、`search_username`、`search_usernames` |

**调用示例**（种子 `@whyyoutouzhele`）：

```
mcp_maigret_collect_accounts(username="whyyoutouzhele")
```

**步骤二禁止**：`web_search` / `web_extract` / `browser_*`、写报告章节、把 Maigret 返回当画像输出。

## 步骤 4 工具对照
| 平台 | 工具 |
|------|------|
| YouTube | **仅** `mcp_youtube_get_channel_stats(channelId=UC…)`；`channelId` 必须是正式 `UC` 开头 ID。Maigret `ids.youtube_channel_id` 若已有 UC，**直接用**，勿再搜。仅有 `@handle`/`youtube.com/@xxx` 时：先 web 解析 UC，解析不到则 **skip** YouTube 子步骤，**禁止**把 handle 当 channelId，**禁止**因其它平台采完就跳过本平台不处理 |
| GitHub | Apify（username / 仓库用户名），gist URL 可抽 username；失败则 skip，勿空跑 |
| 微博 | `mcp_weibo_get_profile` |
| Instagram | `mcp_apify_apify__instagram_scraper` → run → dataset |
| TikTok | `mcp_apify_clockworks__tiktok_scraper` → run → dataset |
| Telegram | `mcp_apify_vujeen__telegram_channel_scraper` → run → dataset |
| Facebook  | `mcp__apify__headlessagent__facebook_profile_post_scraper` → run → dataset |
**步骤4禁止使用**：`get_user_tweets`、`get_user_feeds`、`analyze_channel_videos`、YouTube 搜视频（这些是步骤7发文用）；**禁止**未收口 youtube/github 就进步骤5 vision。

## 步骤 7 可使用工具参考
| 平台        | 工具 |
|-----------|------|
| Twitter   | `mcp__twitter__get_user_tweets` |
| YouTube   | `mcp__youtube__analyze_channel_videos(channelId=UC…)` |
| 微博        | `mcp_weibo_get_user_feeds`（发文；注意不是 get_profile） |
| Instagram | `mcp_apify_apify__instagram_scraper` → run → dataset |
| TikTok | `mcp_apify_clockworks__tiktok_scraper` → run → dataset |
| Telegram  | `mcp_apify_vujeen__telegram_channel_scraper` → run → dataset |
| Facebook  | `mcp__apify__headlessagent__facebook_profile_post_scraper` → run → dataset |

**种子平台硬约束**：无论种子是 Twitter / YouTube / 微博 / Facebook 等，步骤7都必须对该平台再采一轮发文；Apify 种子平台步骤1 dataset 只入主页，步骤7须再 Actor→run→dataset 才能入 posts。

## 步骤 11 输出骨架

**硬约束**：终稿正文**必须以** `## 一、账号基本信息` 或 `一、账号基本信息` 开头（前导空白除外）；**禁止**在第一节之前写进度句、步骤号、图片管线、task_id 等任何元叙述。

```
## 一、账号基本信息
【平台·@handle】MCP/Apify 原文字段…
### 1.1 profile信息:昵称、简介、粉丝数、认证状态、头衔、外链。
### 1.2 发文特征：最早发帖时间、发帖频率、活跃时间段、发文语种、

## 二、账号全网关联账号
### 2.1 强关联账号
### 2.2 关联原因：文本流、图片流

## 三、账号网络活动情况
### 观点1：xxxx。发文作证：1.xx年xx月xx日在XX平台发文称xxxx;2.xx年xx月xx日在XX平台发文称xxxx;3.xx年xx月xx日在XX平台发文称xxxx...
以此类推...

## 四、核查思路
### 1.点位：xxx。依据：xxxxx;
以此类推...
```

## 禁止收尾示例

- ❌ 近期推文主题归纳（无原文）
- ❌ 「如果你想进一步了解…」
- ❌ 结尾不要输出类似报告完毕... 执行完毕... 数据来源等相关描述
- ❌ 步骤 7.5 / 图片管线 / 未部署 / 继续步骤 8（任何元叙述写进终稿）
- ❌ 终稿不以「一、账号基本信息」开头（前缀进度句、管线摘要一律禁止）
- ❌ 终稿内出现 `image_pipeline` / `task_id` / `force-analyze` / ModuleNotFoundError