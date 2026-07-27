---
name: account-intelligence-report
description: "04写报@种子。步骤2仅mcp_maigret_collect_accounts→步骤3网页检索→主页/流/4.2认定/4.3系统社工库ES→发文→7.5图片入库→分析→画像报告。禁search_username。"
version: 1.22.0
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
- **步骤 5（4.1）**：父壳下分 **4.1.1 文本流核验** / **4.1.2 图片流核验**。步骤4全部主页子节点终态后系统启动核验：文本可做规则比对；Agent 应用 `[文本核验结论]` 输出核验正文（无工具）。**4.1.2 由系统 `image_pipeline` 入库分析，完成后才 completed（无图则 skipped）**。两子都终态后父壳 completed 并进步骤6。步骤5未完成禁止发文工具
- **步骤 6（4.2）** 由系统收敛 `validated_accounts`（勿空转宣称完成）；完成前 **禁止**发文工具与 Apify 发文轮
- **步骤 4.3（`step6_osint_es`）**：**主路径由系统自动查 ES**（4.2 完成后 kickoff，不依赖 Agent）。Agent 若见待查 URL 可补调 `mcp_es_search_search_country_wise`；**禁止**默认跑 `search_facebook`/`search_worldpeople`；可选输出 `[社工库核验结论]`。系统无命中 → **skipped**（不阻塞发文）；会话结束/超时有 fail-forward
- **步骤 7 / UI步骤5 发文（硬强制·禁止空过）** 须等 4.3 终态后，对 **每一个** `verdict=validated` 平台（含种子）**本回合必须实际调用对应发文工具**（不是口头宣称）。Twitter→`get_user_tweets`；YouTube→`analyze_channel_videos(channelId)`；微博→`get_user_feeds`；其余 Apify→Actor→dataset。**禁止**未调用就收口/进分析/结束会话；**禁止**空过种子 Twitter/YouTube；步骤1/4 主页≠发文。允许：工具已调用但失败或 0 条再 skip
- **步骤 7 视频（Hook 自动）**：发文入库后若有可下载视频，系统自动挂 `5.1.x.1`（`step7_video_*`）并后台分析（每平台最多 1 条、只取前 180 秒、3 秒一帧）。**禁止**同步调用 `mcp_video2frame_*`。发文子步须等视频终态再收口；无视频不建节点
- **步骤 7.5（硬门槛）**：步骤7发文全部结束后、步骤8之前，必须跑图片资产入库+分析回填（见下）；失败只记日志/摘要，**禁止**因此把整任务判失败，**禁止**跳过直接写步骤8～11
- **硬顺序**：步骤5（4.1.1+4.1.2）→ 步骤6（4.2）→ **4.3 社工库** → 步骤7（含视频子节点终态） → **7.5 补发文配图** → 步骤8/9/10；4.3 未终态时发文工具会被拦截
- **步骤3→4**：web_search 未停轮前不要宣称步骤3完成；步骤4主页工具开始后禁止再 web_search（YouTube 解析 UC 例外见上）
- **步骤5→6→4.3→7**：4.1 两子终态后进 4.2；4.2 后进 4.3；步骤7仅在真正调用发文工具时开始
- **步骤 8、步骤9、步骤10**：在同一次响应内同时发起（并行）；三步分析结果必须完全展示呈现；步骤8可优先结合已入库的 `collect_images` / 图片 API，勿再全量空跑未入库 CDN
- **步骤11** 结合 步骤9、步骤10的分析结果为数据基础。
- **步骤11** 必须按照整体章节的结构输出， 每一章节内容必须使用整段叙述性文字描述。不要换行输出展示
- **步骤11 视频观察**：有平台视频分析成功时，须在「三、账号网络活动情况」写入视频观察（系统落库也会兜底并入）


## 执行流程（硬顺序，不可省略任意一步）
| 步骤 | 动作                                                                                 |
|----|------------------------------------------------------------------------------------|
| 1  | 种子 MCP/Apify profile（Instagram/TikTok/Telegram/Facebook/GitHub 走 Apify 三轮） |
| 2  | **`mcp_maigret_collect_accounts(username=种子)`** 跨平台发现候选；**禁止** search_username 等其它 Maigret 工具 |
| 3  | web_search, web_extract, browser_* 检索种子账号昵称及账号id获取候选社交账号                         |
| 4  | 各候选主页：MCP profile **或** Apify；失败跳过                                        |
| 5  | 4.1 信息核验：4.1.1 文本流核验 + 4.1.2 图片流核验（系统 image_pipeline）                    |
| 6  | 4.2 `validated_accounts`（相似账号认定）                                                 |
| 4.3 | 社工库核验：**系统自动**查 validated 的 TW/FB/LI URL；Agent 可补查；无命中 skipped |
| 7  | 发文：MCP 或 Apify；有可下载视频时 Hook 挂 `5.1.x.1` 后台分析                               |
| 7.5 | **补发文配图**：发文后再次 `image_pipeline`（`skip_if_stored`），补齐发文中图片 |
| 8  | 图片流： 分析账号头像、账号背景图片、账号发文配图的关联信息                                                     |
| 9  | 文本流： 结合 各个平台的账号发文 分析发文观点及涉华发言 并配有发文作为佐证                                            |
| 10 | 文本流： 结合 各个平台的账号发文 分析真实姓名、年龄、籍贯、常住地、活动城市、生活习惯、教育经历、工作经历、对华态度、电话邮箱码值、社交、亲友、同事等三个圈层关系 |
| 11 | 一次输出账号画像报告：一、账号基本信息 二、账号全网关联账号 三、账号网络活动情况（含视频观察若有） 四、核查思路 |

## 步骤 1 种子主页（MCP 或 Apify）

| 平台 | 工具 |
|------|------|
| Twitter/微博/YouTube/B站 | 对应 MCP profile |
| Instagram/TikTok/Telegram/Facebook/GitHub | Apify Actor → `get_actor_run` → `get_dataset_items`（只取主页） |

**步骤1禁止**：发文工具、Maigret、OCR/vision、web_search。

## 步骤 5 / 4.1 信息核验（文本 + 图片子节点）

| 规则 | 说明 |
|------|------|
| 开门 | 仅当步骤4全部 `step4_profile_*` 终态 |
| 4.1.1 | 文本流核验：系统规则比对 + Agent 输出 `[文本核验结论]` 正文（**禁止**调工具收口） |
| 4.1.2 | 图片流核验：系统自动跑 `image_pipeline`；**完成后库中须有可渲染 `collect_images`** 才 completed；无图 → skipped |
| 父壳 4.1 | 两子都终态（completed/failed/skipped）后 completed → 步骤6 |
| 禁止 | 步骤5未完成写步骤6/发文；禁止把 `image_pipeline` 元叙述写进终稿 |
| 禁空等 | 发文工具被门禁拦截时：**禁止结束会话空等**；步骤4未完则继续主页，步骤4已完则等下一轮 `step7_posts` 放行后立刻采发文 |

## 步骤 4.3 社工库核验（系统主路径 + Agent 可选）

| 规则 | 说明 |
|------|------|
| 开门 | 4.1（`step5_streams`）与 4.2（`step6_validated`）均 completed |
| 主路径 | **系统**对 validated 的 Twitter/Facebook/LinkedIn `profile_url` 自动调 ES；有命中 completed，无命中 skipped |
| Agent | 若系统未收口且上下文仍列待查 URL，可补调 `search_country_wise`；可选输出 `[社工库核验结论]` |
| 禁止 | 默认跑 `search_facebook` / `search_worldpeople`；4.3 未终态禁止发文 |
| 成报 | **不单开**「社工库」专节；有命中时把摘要揉进「一、账号基本信息」或「四、核查思路」 |
| 兜底 | 会话结束未调用 ES → skip；超时无 ES 调用 → fail-forward skip |

## 步骤 7.5 图片入库与分析回填（硬门槛）

步骤7发文工具全部结束后、进入步骤8之前必须执行图片资产入库（或确认系统 Hook 已兜底完成）：

1. 取得当前写报 `task_id`（Gateway `task:{uuid}` 或会话活跃任务）。
2. **禁止**用 `search_files` / `web_search`「探测是否部署」；管线在仓库 `scripts/image_pipeline/`，**视为已部署**。
3. 工作目录在 `scripts/`（或 `PYTHONPATH` 含 `scripts`），优先执行：

```bash
python -m image_pipeline.run --task-id <taskId> --force-analyze
```

4. 将 stdout JSON 摘要最多用 **1 句进度**说明（写在思考/进度里，**禁止**写入终稿任一章节）；失败只记日志，**禁止**把整任务判失败，**禁止**跳过直接写步骤8～11。
5. 步骤树**不新增**节点；系统 Hook 会在 7→8 / finalize 漏跑时兜底。Agent 宜主动执行本命令，但**即使命令失败也不准**把 `image_pipeline` / `collect_images` / `步骤7.5` 写进报告正文。

**禁止在报告正文（含「四、核查思路」）写**：步骤 7.5、图片管线、未部署、task_id、Hook、`image_pipeline`、`collect_images`、force-analyze 等任何管线/运维元叙述。
**禁止**因终端报错/找不到会话就自编「管线未找到 / 即席执行 / 跳过步骤 7.5」写进 stream 或终稿；图片入库由系统 Hook 兜底。

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

**步骤7执行硬约束（本回合优先）**：
1. 打开步骤7后，**本回合优先**对上下文「尚未尝试发文」列表逐平台调工具，禁止先写步骤8～11、禁止结束会话空等。
2. 种子 Twitter：**必须** `mcp_twitter_get_user_tweets`（或等价 sanitize 名）；仅有 `get_user_info` 不算发文。
3. 种子 YouTube：**必须** `mcp_youtube_analyze_channel_videos(channelId=UC…)`；仅有 `get_channel_stats` 不算发文。
4. 上下文若列出未尝试平台：非发文工具会被 Hook 拦截，先清列表再谈 7.5/分析。

<!-- ## 步骤 11 输出骨架

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
``` -->
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
### 真实姓名、年龄、性别、体貌特征。依据：xxxxx;
### 常住地。依据：xxxxx;
### 籍贯。依据：xxxxx;
### 活跃城市范围。依据：xxxxx;
### 工作经历，职业、工作单位等。依据：xxxxx;
### 教育经历，学历、专业等。依据：xxxxx;
### 家庭、婚姻情况。依据：xxxxx;
### 疑似电话、邮箱。依据：xxxxx;
### 强关联社交帐号。依据：xxxxx;
### 账号运行方式，个人还是团体。依据：xxxxx;
以此类推...
```

## 禁止收尾示例

- ❌ 近期推文主题归纳（无原文）
- ❌ 「如果你想进一步了解…」
- ❌ 结尾不要输出类似报告完毕... 执行完毕... 数据来源等相关描述
- ❌ 步骤 7.5 / 图片管线 / 未部署 / 继续步骤 8（任何元叙述写进终稿）
- ❌ 终稿不以「一、账号基本信息」开头（前缀进度句、管线摘要一律禁止）
- ❌ 终稿内出现 `image_pipeline` / `task_id` / `force-analyze` / ModuleNotFoundError