# 04写报 · 步骤树时序问题（父壳点亮 / summary 停轮询）

> 状态：问题 1、问题 3 已改代码；问题 2 已定位，待改  
> 样本任务：`2176e502-0faf-4218-8713-9d2a9abd1776`（已手工补标 `completed`）  
> 日期：2026-07-29

---

## 背景

前端轮询 `GET /api/tasks/{taskId}/tree` 渲染步骤树。04 写报树形为：

```text
step_plan
  └── phase_*（七大父壳）
        └── 业务步 / 平台子步（如 step7_posts → step7_post_twitter）
```

另有历史列表「进行中」依赖 `hermes_tasks.status`。近期发现三处时序/收口问题。

---

## 问题 1：发文父壳晚于子节点变为 running

### 现象

「5.1.1 Twitter Agent 发文采集」已 running / 甚至 completed，父壳「5. 内容采集」（`phase_content`）仍长时间 `pending`，很久后才变 `running`；此时子节点往往已全部终态。

样本时间线：

| 节点 | started_at |
|------|------------|
| `step7_post_twitter` | 17:01:17 |
| `phase_content` | 17:03:32 |
| `step7_posts` | 17:03:35 |

发文工具 `mcp_twitter_get_user_tweets` 成功约在 17:01:18。子步领先父壳约 **2 分钟**。

### 根因

1. **深叶不滚壳（设计）**  
   `phases.py` 中 `EXECUTION_PARENT_SHELL` 只映射挂在壳下的**直接**业务步（如 `step7_posts` → `phase_content`）。  
   `step7_post_twitter` 等 5.1.x **不映射**。  
   因此 `set_step_status(step7_post_* → running)` 时，`_ensure_phase_shell_running` 对深叶返回 `None`，**不会**点亮 `phase_content`。

2. **父节点 `step7_posts` 点亮滞后**  
   壳依赖 `step7_posts` 先变 `running` 才会滚壳。  
   子步已 running 时，父节点常未及时 `start_step7_if_ready`；事后靠 `ensure_step7_parent_active`（`pending` + 有子 `running` → 补点父）在 reconcile 里补，故出现空窗。  
   另有 `ensure_step7_awaits_agent_tool` 防抢跑逻辑，可能在时序交错时把父壳打回 `pending`，放大空窗。

### 期望语义

任意 `step7_post_*` 变为 `running` 时，`step7_posts` 与 `phase_content` 应 **先于或同时** 变为 `running`。

### 已改方案（2026-07-29）

1. **链路补全**（`task_store._ensure_phase_shell_running`）：对深叶沿 `parent_step_key` 向上点亮整条链（先中间父，再七大壳）。约定映射见 `phases.immediate_parent_step_key`。  
   - `step7_post_*` → `step7_posts` → `phase_content`  
   - `step4_profile_*` → `step4_profiles` → `phase_account_collect`  
   - `step5_stream_*` → `step5_streams` → `phase_collision`  
2. **防抢跑收紧**（`ensure_step7_awaits_agent_tool`）：已有子步 `running`/`completed` 时禁止把父壳/父节点打回 `pending`。  
3. **收口对称**（2026-07-29 晚间补丁）：深叶点亮壳后，`step7_posts` completed 时必须收口 `phase_content`；`_maybe_complete_phase_shell` 按库内子步判断并沿父链收口；`force_close_step7_posts_if_ready` 后显式再滚一次壳。  

凡 `set_step_status(深叶 → running)` 的路径自动点亮；终态路径对称收口。

---

## 问题 2：summary 先于「7. 报告生成」完成暴露，前端停轮询导致节点画不出

### 现象

前端用 **tree 里是否已有 summary** 决定是否继续轮询。  
终稿路径里 summary 往往先写入，`phase_report`（7. 报告生成）稍后才 completed；前端一看到 summary 就停轮询，后续壳完成态再也拉不到 → 「7. 报告生成」停在未完成态，渲染不出来。

样本时间线：

| 事件 | 时间 |
|------|------|
| `hermes_user_dialogues` summary 写入 | **17:05:52** |
| `step11_report` completed | 17:05:55 |
| `phase_report` completed | **17:06:02** |

代码 `_complete_step11_from_report` 当前顺序为：先 `save_assistant_output`（summary）→ 再 `step11` completed → 再滚 `phase_report`。与上表一致。

### 根因

与「凭 summary 停轮询」的前端契约冲突：

- summary 一旦进库，tree 响应即可带 `summary` → **立刻停轮询**；
- 此时 `phase_report` 可能仍是 pending/running；
- 几秒后壳才 completed，前端已不再请求。

> 说明：若要求「节点完成必须晚于 summary 写入」，会**加重**该竞态。  
> 与停轮询真正匹配的语义是：**summary 对前端可见时，`phase_report` 必须已是终态**（或前端不停、直到壳终态）。

### 期望语义（前端侧）

停轮询时，树上「7. 报告生成」已是 `completed`，且 summary 可用。

### 拟改方案（待实施 · 已选 tree 标志位）

在 tree 响应中**新增标志位**（字段名待定，如 `pollDone` / `tree_ready` / `report_ready`）：

| 条件 | 标志位 |
|------|--------|
| `taskType == account_report` | `summary 有值` **且** `phase_report.status == completed` → `true` |
| 其它类型（01/02/03） | `summary 有值` → `true`（与现网停轮询语义一致） |

前端改为根据该标志位决定是否继续轮询 tree。

### 对 01/02/03 的影响

- tree 接口四类共用；**不需要**增加请求传参。  
- `task_type` 已由 `taskId` 查库得到，响应里已有 `taskType`。  
- **必须按 `taskType` 分支**计算标志位：01/02/03 无 `phase_report`，若统一要求壳 completed，标志位永远 false，会导致一直轮询。  
- 按上表分支后：**不影响另外三类任务**。

可选兜底（未选，仅备忘）：

- A. `_complete_step11_from_report` 先完成 step11/phase_report，再写 summary；  
- B. tree 仅在 `phase_report` 终态后才暴露 `summary` 字段。  

当前优先：**标志位方案**，不动落库顺序亦可先缓解前端问题。

---

## 问题 3：历史列表「进行中」——终稿已出但 `hermes_tasks.status` 仍 running

### 现象

`GET /api/history/tasks?...&taskType=report` 对样本任务显示「进行中」。tree 侧各步已终态、`summary` 已有、`hasAnswer=true`，但任务行：

| 字段 | 值 |
|------|-----|
| `hermes_tasks.status` | `running` |
| `finished_at` | null |
| `current_phase` | 可能已是 `done` |

History 映射本身正确：`status=running` → 进行中。根因在写报侧未及时标 `completed`。

### 根因

1. `_complete_step11_from_report` 轻量落完 summary / step11 后，先跑重路径（step7 reconcile 等），**末尾**才 `_try_finalize_report`。Hook 超时或 continue 占会话时，进程可能在重路径半截退出，任务永久停在 `running`。  
2. `continue_worker` 发现 `has_final_report` 后直接 `return`，**不补标** `completed`。

### 已改方案（2026-07-29）

1. **sink**：summary + step11 + `PHASE_DONE` 后**立刻** `_try_finalize_report(..., light_only=True)`；重路径后再钉一次；若入口时 step11 已 completed，仍补标。  
2. **session_continue**：`_ensure_completed_if_final_report`；`run_continue_worker_job` 在「已有终稿跳过」与 SSE 后发现终稿时均补标。  
3. 样本任务已手工跑一次 `_try_finalize_report` 补为 `completed`。

---

## 相关代码位置（查阅用）

| 主题 | 位置 |
|------|------|
| 壳映射 / 深叶不滚壳 | `scripts/report_04/phases.py` → `EXECUTION_PARENT_SHELL`、`immediate_parent_step_key` |
| 点亮壳（沿父链） | `scripts/report_04/task_store.py` → `_ensure_phase_shell_running`、`_parent_chain_to_phase_shell` |
| 父节点滞后补亮 | `scripts/report_04/step_reconcile.py` → `ensure_step7_parent_active` |
| 防抢跑降级 | `scripts/report_04/step_reconcile.py` → `ensure_step7_awaits_agent_tool` |
| 终稿 summary + step11 | `scripts/report_04/sink.py` → `_complete_step11_from_report` |
| 任务 completed | `scripts/report_04/engine.py` → `_try_finalize_report` |
| 续跑补标 | `scripts/report_04/session_continue.py` → `_ensure_completed_if_final_report` |
| tree 组装 | `clients/hermes-xa/.../TaskTreeQueryService.java` → `buildTaskTree` |

---

## 实施清单

- [x] 问题 3：终稿后立刻标 completed；continue 有终稿时补标  
- [x] 问题 1：子步 running 时沿父链点亮 `step7_posts` + `phase_content`；防抢跑不打回已开跑子步  
- [ ] 问题 2：tree 增加停轮询标志位，`account_report` 双条件、其它类型仅 summary  
- [ ] 与前端约定标志位字段名与取值（`true`/`false`）  
- [ ] 用新跑 04 任务验收：历史在终稿后变为已完成；发文父壳不晚于子步；停轮询标志位按 taskType 分支  

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-07-29 | 初稿：问题 1/2 定位、样本时间线、拟改方案与标志位按 taskType 分支说明 |
| 2026-07-29 | 问题 3：历史「进行中」根因与 sink/continue 补标已落地；样本任务补 completed |
| 2026-07-29 | 问题 1：`_ensure_phase_shell_running` 沿父链点亮；`ensure_step7_awaits_agent_tool` 收紧 |
| 2026-07-29 | 问题 1 补丁：壳收口对称 + 同 session 禁止 Hook 重放建幽灵任务；样本 31f9 phase_content 已补 completed |
