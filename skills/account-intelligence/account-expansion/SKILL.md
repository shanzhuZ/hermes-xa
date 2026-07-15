---
name: account-expansion
description: "账号扩建@种子。种子profile→Maigret跨平台发现→候选主页采集→文本流核查→发文→一次输出四节(扩建收集/多平台采集/账号核查/账号)。禁画像禁web_search。"
version: 1.1.0
author: hermes-xa
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [account-intelligence, expansion]
    related_skills: [account-intelligence-collect]
---

# 账号扩建

**账号扩建任务**，不是画像。从用户指定的一个 @种子 向外发现关联账号并核查，最终只输出四节：
`一、账号扩建收集` `二、多平台信息采集` `三、账号核查` `四、账号`。

## 启动（只读 1 个参考）

`skill_view` → **`references/expansion-rules.yaml`**（唯一参考，禁止再加载其它 references）。

## 铁律

1. **步骤 1～5**：禁止 `web_search` / `web_extract` / `browser_*`；禁止写报告、人物传记、综合介绍
2. **步骤 1～5**：禁止输出「一、」「二、」「三、」「四、」任何一节；最多 2 句进度
3. **种子唯一**：只有用户指定的 @种子 是目标；禁止把 Maigret/简介里其他 handle 当种子
4. **步骤 2 发现**：跑 `mcp_maigret_collect_accounts`；去重后进第一节；失败→记录并继续，不中止；**禁止**按 MCP 返回写画像
5. **步骤 3 采主页 + 发文（平台顺序稳定）**：Maigret 结束后，先按“步骤二已确认的平台列表”建立本轮要采集的平台顺序；之后按平台顺序逐个平台完成采集。每个平台允许在同一步内拿 profile + 发文，但**禁止**只先跑种子 Twitter 发文、再回头补其他平台主页；也**禁止**步骤 4 先于步骤 3 全量采集启动。有 MCP→MCP，无 MCP→Apify；失败重试最多 2 次，三次均失败→跳过并标记"采集失败"；不换工具、不用 web_search
6. **步骤 4** 双流核查（候选 vs 种子），两条流都要做：
   - **文本流**（账号信息 + 发文信息）：昵称 / handle / 简介 / 互链 / 邮箱 / 发文内容，共 6 项逐条对比
   - **图片流**（**只用头像 avatar_url**，不用背景图、不用发文图片）：对步骤 3 profile 里的头像 URL 调 `mcp_ocr_perform_ocr(input_data=头像URL, language="chi_sim")` 提取图内文字 **+** `vision_analyze` 描述画面，候选头像 vs 种子头像做比对（language 只能单码：`chi_sim` 或 `eng`）
   - 综合判定基于文本流 + 图片流；发文内容对比基于步骤 3 采到的发文
7. **第四节只列 HIGH 账号**：仅相似度极高（HIGH）的账号入选，MEDIUM/LOW 一律不展示；每条附一句判断依据；不含 profile、不含发文
8. **步骤 6** 一次性输出四节

## 五步（硬顺序）

| 步 | 动作 |
|----|------|
| 1 | 种子平台 MCP/Apify profile（Instagram/TikTok/Telegram/Facebook/GitHub 走 Apify 三轮） |
| 2 | Maigret MCP（跨平台发现） |
| 3 | 按步骤二的平台顺序，逐个平台采集 profile + 发文（MCP **或** Apify，小 limit）；禁止只先跑单个平台发文 |
| 4 | 文本流（账号信息+发文内容）+ 图片流（**头像 avatar_url** OCR+vision）vs 种子（核查打分） |
| 5 | 判定核查通过账号（**仅 HIGH 纳入**，MEDIUM/LOW 排除）→ 决定第四节列表 |
| 6 | **一次**输出四节 |

## 步骤 1 / 3 工具对照

| 平台 | 工具 |
|------|------|
| Twitter | 步骤1：`mcp_twitter_get_user_info`；步骤3 发文：`get_user_tweets` |
| YouTube | 步骤1：`mcp_youtube_get_channel_stats(channelId=UC…)`；步骤3 发文：`analyze_channel_videos` |
| 微博 | 步骤1：`mcp_weibo_get_profile`；步骤3 发文：`get_user_feeds` |
| B站 | 步骤1：`mcp_bilibili_get_user_info` |
| Instagram / TikTok / Telegram / Facebook / GitHub | 步骤1 与步骤3：**Apify 三轮**（Actor → get_actor_run → get_dataset_items）；步骤1 只取 profile，步骤3 再采发文 |

**步骤 1**：有 MCP 用 MCP；无 MCP 必须 Apify 三轮，禁止因「无 MCP」跳过。
**步骤 3 同时用两类工具**：profile 类 + 发文类（MCP 或 Apify），一次采完，确保 profile 与发文同源。**仍禁止**：YouTube 搜视频（只对已知 channelId 采）、`web_search`。

## 步骤 7 输出骨架

```
（可选1句：已为 @种子 完成账号扩建，发现 M 个候选，核查通过 N 个。）

## 一、账号扩建收集
（Maigret MCP 发现的原始候选，未核查）
每行格式：平台：xxxx | URL：xxxx
禁止输出：发现来源、是否有 MCP、序号、分隔线

## 二、多平台信息采集
（每个账号：profile 原文字段 + 该账号发文原文，合并在同一块）
【平台·@handle】
  - profile 原文字段…
  - 发文：逐条 MCP/Apify 原文…

## 三、账号核查
### 3.1 核查（每个候选平台一块，包含：文本流 + 图片流对比 + 相似度分析 + 判定等级）
格式：
【平台·@handle】判定等级：HIGH/MEDIUM/LOW
文本流（账号信息 + 发文信息）：
- 昵称：xxx（vs 种子：xxx）→ 相似度/差异说明
- handle：xxx（vs 种子：xxx）→ 相似度/差异说明
- 简介：xxx（vs 种子：xxx）→ 相似度/差异说明
- 互链：xxx（vs 种子：xxx）→ 相似度/差异说明
- 邮箱：xxx（vs 种子：xxx）→ 相似度/差异说明
- 发文内容：分析候选平台发文的主题、风格、观点是否与种子发文一致（基于步骤3采集的发文）
图片流（只用头像 avatar_url，不用背景图、不用发文图片）：
- 头像：OCR 文字（候选：xxx vs 种子：xxx）+ 视觉描述（候选：xxx vs 种子：xxx）→ 相似度/差异说明（基于步骤4对头像 URL 做的 mcp_ocr_perform_ocr + vision_analyze；无头像则注明"无头像"）
- 综合判定：基于文本流 + 图片流的综合分析，给出 HIGH/MEDIUM/LOW 判定理由

禁止：单独的 3.2 核查判定小节、维度横向对比表格、分析背景图或发文图片（图片流只针对头像）

## 四、账号
（相似度极高的账号列表——只保留 HIGH（含种子），MEDIUM/LOW 一律不展示；每条附一句判断依据）
- 【平台·@handle】种子
- 【平台·@handle】HIGH — 判断依据：xxx（一句话概括文本流/图片流里最关键的命中，如"昵称+handle 完全一致，头像同图，发文同调"）
```

## 与四节的对应关系

- 一节 = 步骤 2 发现的原始候选（不做任何核查判断）
- 二节 = 步骤 3 一次采到的 profile **+** 发文（同源，profile 与发文合并同块）
- 三节 = 步骤 4/5 的核查块（文本流 6 项 + 头像图片流）+ 判定
- 四节 = 步骤 5 判定为 HIGH 的账号（+种子）列表，每条附判断依据（**MEDIUM/LOW 不展示，不含 profile、不含发文**）

## 禁止收尾示例

- ❌ 🧾 综合画像报告 / 用户画像总结 / 核心身份 / 发展轨迹
- ❌ 近期推文主题归纳（无原文）
- ❌ 「如果你想进一步了解…」
