---
name: account-intelligence-collect
description: "01采集@种子。Maigret后MCP/Apify只采主页→流比对→相似账号采发文→一次输出三节。禁画像禁web_search。"
version: 1.12.0
author: hermes-xa
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [account-intelligence, collect]
    related_skills: []
---

# 01 · 账号信息采集

**数据采集任务**，不是画像。最终只输出三节：`一、个人信息` `二、账号核验依据` `三、发文信息`。

## 启动（只读 1 个参考）

`skill_view` → **`references/collect-rules.yaml`**（唯一参考，禁止再加载其它 references）。

## 铁律

1. **步骤 1～6**：禁止 `web_search` / `web_extract` / `browser_*`；禁止写报告、人物传记、综合介绍
2. **步骤 1～6**：禁止输出「一、」「二、」「三、」任何内容；最多 2 句进度
3. **Maigret 返回后**：读 `summary.accounts` + `agent_must_do_next`（若有），**禁止**按 MCP 返回写画像
4. **步骤 3 只采主页**：有 MCP→profile；无 MCP→Apify；**失败就跳过**，不换工具
5. **步骤 4** 必须 OCR+vision（有头像）+ 2.1/2.2 表数据
6. **步骤 6** 才对 `validated_accounts` 采发文
7. **步骤 7** 一次性按 `collect-rules.yaml` 输出三节

## 六步（硬顺序）

| 步 | 动作 |
|----|------|
| 1 | 种子 MCP profile |
| 2 | Maigret（跨平台时） |
| 3 | 各候选主页：MCP profile **或** Apify（小 limit）；失败跳过 |
| 4 | 文本流+图片流 vs 种子 |
| 5 | `validated_accounts`（相似账号） |
| 6 | 发文：MCP 或 Apify |
| 7 | **一次**输出三节 |

## 步骤 3 工具对照

| 平台 | 工具 |
|------|------|
| YouTube | `mcp_youtube_get_channel_stats(channelId=UC…)` |
| 微博 | `mcp_weibo_get_profile` |
| Instagram | `mcp_apify_apify__instagram_scraper` → run → dataset |
| TikTok | `mcp_apify_clockworks__tiktok_scraper` → run → dataset |
| Telegram | `mcp_apify_vujeen__telegram_channel_scraper` → run → dataset |

**禁止步骤 3**：`get_user_tweets`、`get_user_feeds`、`analyze_channel_videos`、YouTube 搜视频。

## 步骤 7 输出骨架

```
（可选1句：已为 @种子 完成采集，纳入 N 个账号。）

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

- ❌ 🧾 综合画像报告 / 用户画像总结 / 核心身份 / 发展轨迹
- ❌ 近期推文主题归纳（无原文）
- ❌ 「如果你想进一步了解…」
