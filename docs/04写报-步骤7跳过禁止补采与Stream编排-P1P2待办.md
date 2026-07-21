# 04 写报：步骤 7 子步 skipped 禁止补采 + Stream 编排（P1/P2 待办）

**状态**：待实施（**视频接入完成后再改**；图片资产已于 2026-07-21 接入 04，本待办与图片解耦）  
**关联**：`docs/04写报编排与白名单-v1.md`（引擎 v2 P0 已落地）、`skills/account-intelligence/account-intelligence-report/SKILL.md`  
**典型案例**：任务 `9be5b124-281e-4646-a50c-7564db3a4f3a` — `7.5`（`step7_post_telegram`）先 **skipped**，步骤 7 **父节点 completed**，编排 gate 已应进入 **步骤 8**，但 Stream 仍展示 **7.5 发文采集**；补采完成后 7.5 变 **completed**，8～11 才正常推进。

> 说明：Skill 流程里的「步骤 7.5 图片资产」与步骤树节点 `7.5`（`step7_post_telegram`）是两回事——前者是图片管线，后者是 Telegram 发文子步。

---

## 1. 产品决策（已定）

**策略 A（最干净）**：**某步骤 7 子节点一旦为 `skipped`，禁止再对该平台做任何发文类 MCP/Apify 补采。**

- 子步 `skipped` + 父 `completed` → **硬拦截**该平台 `step7_post_*` 对应工具。
- **不再**使用「步骤 7 父已收口仍允许补采发文」的宽口径。
- Agent 侧：**`pre_llm` 注入**明确文案，例如：「`7.5`（Telegram）已跳过，禁止再 Apify/MCP 发文；请直接进入步骤 8/9/10。」
- **P1**：思考流 / SSE 高亮步骤与 DB **`infer_gate_step`** 一致（步骤 7 收口后应显示 **8**，而不是工具 phase 仍挂在 `step7_post_*`）。
- **P2**：Gateway / Skill 与引擎 **`allowed_tools`** 对齐，模型**调不了**被禁的发文工具。

**不在本次范围（可选后续）**：

- 「补采则把步骤 7 父 reopen 为 running」— **不采用**。
- 「减少误 skip 7.5」— 可另开 reconcile 收紧单，与本策略并行但不替代硬拦补采。

---

## 2. 现状与根因（为何会出现 7.5 skipped 仍采）

| 机制 | 位置 | 行为 |
|------|------|------|
| 步骤 7 收口后仍允许发文 | `scripts/report_04/orchestrator.py` → `_allow_late_step7_post_collect` | 父 `completed/skipped` 时 **放行** POST / Apify 发文工具 |
| 父节点关闭条件 | `scripts/report_04/step_reconcile.py` → `close_collect_parent_if_ready` | 子节点 **含 skipped** 即算终态，父可 **completed** |
| 补采后子步回写 | `reconcile_step4_and_step7_children` / `save_post_rows` | `force_reopen` 可把 **skipped → completed**，父 **默认禁止** completed→running |
| Stream 信号 | Java `CoarseStepSync`（04 已禁写库）+ 工具事件 phase | 前端仍可能按 **工具名/phase** 展示「步骤 7.x」，与 DB gate **脱节** |
| Skill | `account-intelligence-report/SKILL.md` 步骤 7 | 模型仍按文案去采各平台发文，与「子步已 skip」无绑定 |

---

## 3. 实施顺序（图片/视频接入后）

建议 **同一分支** 内按序落地，避免只改 Python 门禁、Stream 仍误导：

1. **Python 硬门禁 + pre_llm 文案**（行为真相源）
2. **P1 Stream / 前端 gate**（展示对齐 DB）
3. **P2 allowed_tools + Skill 一句硬约束**（模型侧双保险）
4. 回归 fixture：`9be5b124` 类时间线 + 至少一条「7.x skipped、7 父 completed」用例

---

## 4. 改动清单（按文件）

### 4.1 Python — 去掉「收口后补采」（核心）

| 文件 | 改什么 |
|------|--------|
| `scripts/report_04/orchestrator.py` | **删除或重写** `_allow_late_step7_post_collect`：默认 **不再**因「步骤 7 父 completed」放行发文。`block_tool_reason` 中增加：**若 `step7_posts` 已 completed/skipped 且目标平台子步 `step7_post_{platform}` 为 skipped → 返回拦截原因**（Apify `get_dataset_items` 需结合 `phase` / `platform_hint` 判平台）。 |
| `scripts/report_04/sink.py` | `_on_pre_tool`：与 orchestrator 规则一致；对 `mcp_apify_get_actor_run` / dataset 若 phase 为 `step7_post_*` 且该子步 skipped → **block**。`_is_premature_step7_tool` 仅管 step5/6 未完成，**不替代**本规则。 |
| `scripts/report_04/sink.py` | `_on_pre_llm` / `_step5_guidance_context` 或 **`engine.build_agent_context`**：当 gate 为 `step8_*`～`step11_*` 或步骤 7 父已终态时，**枚举仍为 skipped 的 `step7_post_*`**，注入「禁止再发文、进入步骤 8」列表（含 **step_node**，如 `7.5`）。 |
| `scripts/report_04/engine.py` | 扩展 `build_agent_context` / 新增 `list_skipped_step7_platforms(task_id)`；`pre_tool_allowed` 调用更新后的 `block_tool_reason`；可选输出 **`allowed_tools`** 快照供 Gateway（P2）。 |
| `scripts/report_04/gates.py` | 可选：新增 `is_step7_post_platform_skipped(task_id, platform) -> bool`，供 orchestrator/sink 复用。 |
| `scripts/report_04/step_reconcile.py` | **审查** `_maybe_skip_step7_actor_stale`、空 dataset 即 skip：文档化 skip 原因；**不**在 reconcile 里对 skipped 子步再 `force_reopen` 为 completed（与「禁止补采」一致）。若 posts 晚到入库，**丢弃或只入 collect_posts 不改子步终态**（需产品确认，建议：**晚到也视为违规路径，不入库或入库但不改 skipped**）。 |
| `scripts/report_04/task_store.py` | `set_step_status(..., force_reopen=True)` 用于 step7 子步时：**禁止**在父 completed 且策略 A 开启后把 skipped 子步 reopen（可加 env `HERMES_REPORT_ALLOW_STEP7_LATE_COLLECT=1` 仅运维修复用）。 |

**环境变量（建议）**：

- 默认：禁止步骤 7 补采（策略 A）。
- `HERMES_REPORT_ALLOW_STEP7_LATE_COLLECT=1`：恢复旧行为（仅手工修数 / replay），**正常写报不设**。

---

### 4.2 P1 — Stream 显示步骤 8（与 DB gate 一致）

| 文件 | 改什么 |
|------|--------|
| `clients/.../stream/ThoughtStreamHub.java` | 推送 SSE 时增加字段，例如 **`gateStep`**（来自 DB 或 Hook 缓存），**高亮步骤以 gate 为准**，工具事件仅作「正在执行 xxx 工具」副文案，**不**单独把 UI 步骤拉回 `step7_post_*`（步骤 7 父已 completed 时）。 |
| `clients/.../gateway/HermesGatewayClient.java` | 若 Gateway 转发 SSE，透传 `gateStep` / `engineVersion`。 |
| `clients/.../collect/service/TaskTreeQueryService.java` | 可选：树接口返回 `currentGateStep`，供前端与 Stream 同源。 |
| `scripts/db_sink.py` / Hook | `pre_llm` 返回的 `context` 中可带 `gateStep` 供 Agent；若 Gateway 能读 Hermes session 元数据，写入供 Java 读（二选一，文档实施时定方案）。 |
| **前端**（仓库外若独立） | 订阅 SSE 时 **优先 `gateStep`**；步骤 7 父 completed 后不再展示「当前步骤 7.5」除非 gate 仍为 `step7_posts`（策略 A 下不应出现）。 |

**验收**：步骤 7 父 completed 且 gate=`step8_img_analysis` 时，Stream **主步骤标签为 8**，即使用户看到 Apify 工具名（若误调应已被 pre_tool 拦截，Stream 不应再出现成功采 7.5）。

---

### 4.3 P2 — 模型调不了工具（allowed_tools）

| 文件 | 改什么 |
|------|--------|
| `scripts/report_04/engine.py` | 新增 `allowed_tools(task_id) -> List[str]`：由 `infer_gate_step` + 白名单 `_STEP_WHITELIST` 生成；**剔除** skipped 平台对应 POST/APIFY 工具。 |
| `scripts/report_04/sink.py` | `pre_llm` 返回 JSON 增加 `allowed_tools`（若 db_sink / Gateway 协议支持）。 |
| `skills/account-intelligence/account-intelligence-report/SKILL.md` | 增加一条：**步骤 7 某平台子节点为 skipped 后，禁止再调该平台发文工具；系统 gate 进入步骤 8 后仅允许分析/成稿。** |
| **Gateway / Hermes Agent**（若需） | `pre_tool` 除 Python Hook 外，可读 session 内 `allowed_tools` 做二次校验（实施时评估是否必要；Python Hook 已足够时 Skill + pre_tool 即可）。 |

---

### 4.4 文档与回归

| 文件 | 改什么 |
|------|--------|
| `docs/04写报编排与白名单-v1.md` | 增加「步骤 7 skipped 禁止补采」链接本节；删除或注明「步骤 7 已收口仅允许补采发文」旧表述。 |
| `docs/排查经验汇总-步骤树状态机与Apify稳定性-2026-07.md` | 追加案例 `9be5b124` 与策略 A。 |
| 新增 `scripts/report_04/tests/` 或 replay | 断言：`step7_post_telegram=skipped` + `step7_posts=completed` 时，`block_tool_reason(telegram 发文工具)` 非空；gate=`step8_img_analysis`。 |

---

## 5. 验收标准（策略 A + P1 + P2）

1. **门禁**：`step7_posts` 为 completed，且 `step7_post_{platform}` 为 **skipped** 时，该平台一切步骤 7 发文工具（含 Apify 三轮）**pre_tool block**，reason 含平台/step_node。
2. **pre_llm**：注入文案列出 skipped 的 7.x，并明确要求 **进入步骤 8**（并行 8/9/10 按 Skill）。
3. **数据**：上述场景下 **不应**再新增该平台 `collect_posts`（或新增但不改 skipped→completed，二选一须在实施前写死；**推荐：硬拦工具，posts 不应新增**）。
4. **Stream（P1）**：步骤 7 父 completed 后，SSE **gateStep** 为 `step8_*`（或当前分析步），**主 UI 步骤不是 7.5**。
5. **Skill/P2**：模型在 skipped 场景下 **无法**通过 pre_tool 成功调用被禁发文工具（日志无成功 `post_tool` 入库）。

---

## 6. 与图片 / 视频接入的边界

- **图片管线**（`image_pipeline`、`image-asset-analysis`）：**04 已接入**（步骤 7 发文后 → Skill 7.5 / Hook 兜底）。与本待办「skipped 禁止补采」解耦，可独立验收。
- **视频（YouTube 等）**：若未来步骤 7/8 增加视频下载或分析工具，在 **allowed_tools / 白名单** 中单独列类，**仍遵守**「对应 `step7_post_*` 已 skipped 则禁止该平台采集类工具」同一规则。

---

## 7. 实施前检查项

- [x] 图片接入里程碑完成（04 步骤 7.5，2026-07-21）
- [ ] 视频接入里程碑（可选，与本待办可并行或先后）
- [ ] 确认运维是否需要 `HERMES_REPORT_ALLOW_STEP7_LATE_COLLECT=1` 修历史任务。
- [ ] 前端 / SSE 消费方确认可接 `gateStep` 字段。
- [ ] 与产品确认：**skipped 子步晚到 dataset** 是否一律丢弃（推荐是，与策略 A 一致）。

---

*文档版本：2026-07-21 · 策略 A（7.x skipped 禁止补采）+ P1 Stream gate + P2 allowed_tools · **尚未编码***
