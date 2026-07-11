---
name: account-intelligence-report
description: "04写报@种子。步骤2仅mcp_maigret_collect_accounts→步骤3网页检索→主页/流/发文→分析→画像报告。禁search_username。"
version: 1.13.0
author: hermes-xa
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [account-intelligence, report]
    related_skills: []
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
- **仅步骤 3 允许** `web_search` / `web_extract` / `browser_*`；**其余所有步骤（1、2、4～11）禁止**这三类工具；全程禁止写报告、人物传记、综合介绍（步骤11 的最终画像报告除外）
- **步骤 1、2、4～11**：详细描述执行流程
- **Maigret 返回后**：读 `summary.accounts` + `agent_must_do_next`（若有），**禁止**按 MCP 返回写画像
- **步骤3用浏览器 + 搜索引擎** 搜索类似昵称的账号及账号ID， 严查推特（X）、facebook、telegram、youtube、github、reddit、weibo、linkedin、ins、vk等中大型社交网站
- **步骤 2 与步骤 3 的候选合并去重**：Maigret 候选 + web_search 候选按 平台+handle 去重，形成统一候选列表供步骤4遍历
- **步骤 4 只采主页**：有 MCP→profile；无 MCP→Apify；**失败就跳过**，不换工具
- **步骤 5** 必须 OCR+vision（有头像）；核查结果作为步骤11输出 2.2/2.3 的数据基础（步骤5本身不输出报告章节）
- **步骤 7** 才对 `validated_accounts` 采发文，发文采集范围最近90天
- **步骤 8、步骤9、步骤10**：在同一次响应内同时发起（并行）；三步分析结果必须完全展示呈现
- **步骤11** 结合 步骤9、步骤10的分析结果为数据基础。
- **步骤11** 必须按照整体章节的结构输出， 每一章节内容必须使用整段叙述性文字描述。不要换行输出展示


## 执行流程（硬顺序，不可省略任意一步）
| 步骤 | 动作                                                                                 |
|----|------------------------------------------------------------------------------------|
| 1  | 种子 MCP profile                                                                     |
| 2  | **`mcp_maigret_collect_accounts(username=种子)`** 跨平台发现候选；**禁止** search_username 等其它 Maigret 工具 |
| 3  | web_search, web_extract, browser_* 检索种子账号昵称及账号id获取候选社交账号                         |
| 4  | 各候选主页：MCP profile **或** Apify；失败跳过                                        |
| 5  | 文本流+图片流 vs 种子                                                                      |
| 6  | `validated_accounts`（相似账号）                                                         |
| 7  | 发文：MCP 或 Apify                                                                     |
| 8  | 图片流： 分析账号头像、账号背景图片、账号发文配图的关联信息                                                     |
| 9  | 文本流： 结合 各个平台的账号发文 分析发文观点及涉华发言 并配有发文作为佐证                                            |
| 10 | 文本流： 结合 各个平台的账号发文 分析真实姓名、年龄、籍贯、常住地、活动城市、生活习惯、教育经历、工作经历、对华态度、电话邮箱码值、社交、亲友、同事等三个圈层关系 |
| 11 | 一次输出账号画像报告：一、账号基本信息 二、账号全网关联账号 三、账号网络活动情况 四、核查思路                                   |

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
| YouTube | `mcp_youtube_get_channel_stats(channelId=UC…)` |
| 微博 | `mcp_weibo_get_profile` |
| Instagram | `mcp_apify_apify__instagram_scraper` → run → dataset |
| TikTok | `mcp_apify_clockworks__tiktok_scraper` → run → dataset |
| Telegram | `mcp_apify_vujeen__telegram_channel_scraper` → run → dataset |
| Facebook  | `mcp__apify__headlessagent__facebook_profile_post_scraper` → run → dataset |
**步骤4禁止使用**：`get_user_tweets`、`get_user_feeds`、`analyze_channel_videos`、YouTube 搜视频（这些是步骤7发文用）。

## 步骤 7 可使用工具参考
| 平台        | 工具 |
|-----------|------|
| Twitter   | `mcp__twitter__get_user_tweets` |
| YouTube   | `mcp__youtube__analyze_channel_videos(channelId=UC…)` |
| 微博        | `mcp_weibo_get_user_feeds`（发文；注意不是 get_profile） |
| Instagram | `mcp_apify_apify__instagram_scraper` → run → dataset |
| TikTok    | `mcp_apify_clockworks__tiktok_scraper` → run → dataset |
| Telegram  | `mcp_apify_vujeen__telegram_channel_scraper` → run → dataset |
| Facebook  | `mcp__apify__headlessagent__facebook_profile_post_scraper` → run → dataset |

## 步骤 11 输出骨架

```
（可选1句：已为 @种子 完成采集，纳入 N 个账号。）

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