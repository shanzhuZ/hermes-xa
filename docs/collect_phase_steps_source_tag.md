# collect_phase_steps.source_tag 对照表

> 实现依据：本文件 + `scripts/common/source_tag.py` + `SourceTag.java`。  
> 库内 JSON 列表文本：`["付费"]` / `["开源"]` / `["自研"]` / `["自研","离线"]` / `NULL`。  
> 树接口 `sourceTag` 透出为数组（非字符串）。

---

## 1. 目标

在 `collect_phase_steps` 增加标志位字段，标识采集/检索通道或自研研判类型，供前端展示。

| 节点标题示例 | source_tag |
|--------------|------------|
| Facebook Agent 主页采集 | `["付费"]` |
| Twitter Agent 主页采集 | `["开源"]` |
| 文本流 Agent 对比 | `["自研"]` |
| 社工库核验 Agent | `["自研","离线"]` |

---

## 2. 字段约定

| 项 | 约定 |
|----|------|
| 列名 | `source_tag` |
| 类型 | `VARCHAR(64) NULL`（JSON 文本） |
| 取值 | JSON 数组；元素为 `付费` / `开源` / `离线` / `自研` / `特色` |
| `NULL` 含义 | 阶段壳、父汇总、`step_plan` 等，不打标 |
| 历史任务 | 不强制回填；SQL 018 可将旧单字符串规范为单元素数组 |
| 树接口 | `sourceTag`: `string[]` 或 `null` |
| 范围 | 01 / 02 / 03 / 04（不含 05） |

---

## 3. 平台通道规则（动态子节点共用）

与现网工具通道一致（见 `scripts/collect_01/seed_platforms.py`）：

| 平台 | 通道 | source_tag |
|------|------|------------|
| twitter / weibo / youtube / bilibili | MCP | `["开源"]` |
| facebook / instagram / tiktok / telegram / github | Apify | `["付费"]` |

动态 `step_key` 模式：

| 模式 | 业务 | 标题示例 |
|------|------|----------|
| `step4_profile_{platform}` | 04 | Twitter Agent 主页采集 |
| `step3_profile_{platform}` | 03 | Twitter Agent 主页采集 |
| `step6_post_{platform}` | 01（挂 step6_posts）/ 02（挂 step3_profiles） | Twitter Agent 发文采集 |
| `step3_post_{platform}` | 03 | Twitter Agent 发文采集 |
| `step7_post_{platform}` | 04 | Twitter Agent 发文采集 |
| `step6_video_*` / `step7_video_*` | 01/04 | *视频分析 → `["自研"]` |

种子步 `step1_seed`：按 `seed_json.platform` 套上表（仅开源/付费，不叠自研）。

---

## 4. 分业务对照表

### 4.1 01 账号采集

| step_key | 标题示例 | source_tag | 说明 |
|----------|----------|------------|------|
| step1_seed | Twitter MCP Agent 采集 / Apify Agent · Facebook 采集 | `["开源"]` 或 `["付费"]` | 按种子平台 |
| step2_cross_platform | Maigret Agent 跨平台收集 | `["开源"]` | |
| step3_profiles | MCP/Apify Agent 候选主页采集 | NULL | 父汇总 |
| step3_streams | 信息核验流 Agent 拆分 | NULL | 父壳 |
| step4_text_compare | 文本流 Agent 对比 | `["自研"]` | 纯研判 |
| step4_image_compare | 图片流 Agent 分析 | `["自研"]` | 纯研判 |
| step5_validated | 可信账号核验 Agent | `["自研"]` | 纯研判 |
| step6_posts | 跨平台发文采集 Agent | NULL | 父汇总 |
| step6_post_* | 各平台 Agent 发文采集 | `["开源"]` / `["付费"]` | 动态 |
| step6_video_* | *视频分析 | `["自研"]` | 动态 |

### 4.2 02 账号扩建

| step_key | 标题示例 | source_tag | 说明 |
|----------|----------|------------|------|
| step1_seed | 同 01 | `["开源"]` 或 `["付费"]` | 按种子平台 |
| step2_cross_platform | Maigret Agent 跨平台收集 | `["开源"]` | |
| step3_profiles | MCP/Apify Agent 主页与发文采集 | NULL | 父汇总 |
| step3_streams | 信息核验流 Agent 拆分 | NULL | 父壳 |
| step4_text_compare | 文本流 Agent 对比 | `["自研"]` | 纯研判 |
| step4_image_compare | 图片流 Agent 分析 | `["自研"]` | 纯研判 |
| step5_validated | 可信账号核验 Agent | `["自研"]` | 纯研判 |
| step6_post_* | 各平台 Agent 发文采集 | `["开源"]` / `["付费"]` | 同平台通道表 |

### 4.3 03 账号核查

| step_key | 标题示例 | source_tag | 说明 |
|----------|----------|------------|------|
| step1_input_accounts | 种子账号确认 Agent | `["自研"]` | |
| step3_profiles | MCP/Apify Agent 主页与发文采集 | NULL | 父汇总 |
| step3_profile_* | Twitter / Facebook Agent 主页采集 等 | `["开源"]` / `["付费"]` | |
| step3_post_* | Twitter / Facebook Agent 发文采集 等 | `["开源"]` / `["付费"]` | |
| step3_streams | 发文风格与领域归纳 Agent | `["自研"]` | 纯研判（与 01/02 同 key 但业务不同） |
| step4_text_compare | 文本流 Agent 对比 | `["自研"]` | |
| step4_image_compare | 图片流 Agent 分析 | `["自研"]` | |
| step5_validated | 账号核验 Agent | `["自研"]` | |

### 4.4 04 写报

| step_key | 标题示例 | source_tag | 说明 |
|----------|----------|------------|------|
| step_plan | 制定执行计划 | NULL | |
| phase_* | 1～7 阶段壳 | NULL | |
| step1_seed | 同 01 | `["开源"]` 或 `["付费"]` | |
| step2_maigret | Maigret Agent 跨平台收集 | `["开源"]` | |
| step3_web_search | 网页检索 Agent 候选发现 | `["开源"]` | |
| step4_profiles | MCP/Apify Agent 候选主页采集 | NULL | 父汇总 |
| step4_profile_* | Twitter / Facebook Agent 主页采集 等 | `["开源"]` / `["付费"]` | |
| step5_streams | 信息核验流 Agent 核查 | NULL | 父壳 |
| step5_stream_text | 文本流核验 Agent | `["自研"]` | |
| step5_stream_image | 图片流核验 Agent | `["自研"]` | |
| step6_validated | 相似账号认定 Agent | `["自研"]` | |
| step6_osint_es | 社工库核验 Agent | `["自研","离线"]` | |
| step6_rumor_sx | 陕西谣言特色库 Agent | `["离线","自研","特色"]` | 演示假节点 |
| step7_posts | 跨平台发文采集 Agent | NULL | 父汇总 |
| step7_post_* | 各平台 Agent 发文采集 | `["开源"]` / `["付费"]` | |
| step7_video_* | *视频分析 | `["自研"]` | |
| step8_img_analysis | 图片流 Agent 分析 | `["自研"]` | |
| step9_context_views | 观点与涉华分析 Agent | `["自研"]` | |
| step10_context_pii | PII 与圈层分析 Agent | `["自研"]` | |
| step11_report | 画像报告 Agent | `["自研"]` | |

---

## 5. 已落地文件

| 层 | 路径 |
|----|------|
| SQL | `017_…source_tag.sql`（建列）、`018_…source_tag_list.sql`（扩列+旧值规范化） |
| Python 规则 | `scripts/common/source_tag.py`（`source_tag_json` 写入） |
| Java 规则 | `SourceTag.java`（`jsonForStep` / `parseStored`） |
| Java 写入/透出 | CreateServices + `CollectTaskMapper` + `TaskTreeQueryService` |
| Python 写入 | `collect_01` / `expand_02` / `verify_03` / `report_04` 的 `task_store.py`、`video_job.py` |

上线前请执行 SQL 017（若尚未执行）与 018。
