---
name: account-intelligence-custom
description: "05自定义。自然语言开任务；先出动态步骤计划再执行；步骤用 flow_cli/API 与图对齐；不限主题；可检索浏览；终稿中文。"
version: 0.1.0
author: hermes-xa
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [account-intelligence, custom]
    related_skills: []
---

# 05 · 自定义任务

**语言**：用户中文输入时，**全程简体中文**（进度说明与终稿）。

**目标**：不限制提问主题；由你自主规划工具与步骤；前端步骤树必须与真实执行一致。

## 启动（只读 1 个参考）

`skill_view` → **`references/flow-contract.yaml`**（流程图写库契约；禁止跳过）。

## 铁律

0. **先计划、再执行（不可跳过）**：即使问题很简单、无需搜索，也**禁止**直接输出终稿。必须先：
   1) `upsert` 至少 1 个业务节点（如 `synthesize`「汇总结论」）；
   2) `finish step_plan`；
   3) 对该业务节点 `begin` →（可选工具）→ `finish`；
   4) **最后**才输出终稿正文。
0b. **图=操作**：对某个 `step_key` 调用业务工具（搜索/浏览/MCP/采集等）之前，**必须**先 `begin` 该步；做完后 **必须** `finish`（completed/failed/skipped）。未出现在图上的工作：先 `upsert` 追加节点，再 begin。
0c. **允许追加**：中途发现计划不够，用 `upsert mode=merge` 追加节点后继续；不要悄悄干图外的活。
0d. **禁止 clarify 空转**：能自己决策就做；确缺关键信息最多问 1 次，否则按合理假设继续并在终稿注明。
0e. **TASK_ID 必带**：所有 flow_cli 命令必须带当前任务 UUID（来自 Gateway `task:{taskId}` 或用户/系统给出的 taskId）；不知道就先用 terminal 查库，**禁止空跑**。
1. **工具**：允许 `web_search` / `web_extract` / `browser_*` / 现有 MCP 与 terminal；按问题选型，不要套用 01～04 固定六步。纯知识问答也允许 0 次业务检索，但仍须走 0 的 flow 四步。
2. **进度话术**：执行中最多短进度，不要提前输出终稿结构。
3. **终稿**：最后一次性输出完整回答（可用小标题）；写完后确保业务步骤多为终态，`step_plan` 已 completed。

## 推荐节奏（plan_and_run）

| 序 | 动作 |
|----|------|
| 1 | 理解用户问题；列出 1～8 个可执行步骤（含中文 title；纯问答也至少要有 `synthesize`） |
| 2 | `upsert` 写入计划节点；`finish step_plan` |
| 3 | 循环：`begin` → 调工具干活（可无工具）→ `finish`；不够则 `upsert` 追加 |
| 4 | 汇总证据，输出终稿 |

### 纯知识问答最小示例（本题也必须走 flow）

```bash
python -m custom_05.flow_cli upsert --task-id "$TASK_ID" --mode merge --steps-json '[{"stepKey":"synthesize","title":"汇总结论","stepOrder":20,"stepNode":"2"}]'
python -m custom_05.flow_cli finish --task-id "$TASK_ID" --step-key step_plan --status completed --message 计划已生成
python -m custom_05.flow_cli begin --task-id "$TASK_ID" --step-key synthesize --message 组织回答
python -m custom_05.flow_cli finish --task-id "$TASK_ID" --step-key synthesize --status completed --message 回答完成
# 然后才输出终稿正文
```

## 写流程图（优先用 terminal + Python）

工作目录：仓库 `scripts/`（或保证 `PYTHONPATH` 含 `scripts`）。`TASK_ID` 取当前任务 ID（Gateway `task:{taskId}`）。

```bash
python -m custom_05.flow_cli upsert --task-id "$TASK_ID" --mode merge --steps-json '[{"stepKey":"research","title":"检索公开信息","stepOrder":20,"stepNode":"2"},{"stepKey":"synthesize","title":"汇总结论","stepOrder":30,"stepNode":"3"}]'

python -m custom_05.flow_cli finish --task-id "$TASK_ID" --step-key step_plan --status completed --message 计划已生成

python -m custom_05.flow_cli begin --task-id "$TASK_ID" --step-key research --message 开始检索
# … 调用 web_search 等 …
python -m custom_05.flow_cli finish --task-id "$TASK_ID" --step-key research --status completed --message 已收集线索
```

等价 HTTP（Java 已开机时）：见 `flow-contract.yaml` 的 `http` 段。

## 与 01～04 的关系

- **不要**加载 collect/expand/verify/report 的固定步骤规则来硬套流程。
- 若用户问题明显是「按采集/扩建/核查/写报标准流程」且更适合那类，可在终稿中说明；本任务仍按自定义动态图执行完毕。
- 业务宽表（profiles/posts）第一期可不写；优先保证步骤树 + 对话终稿。

## 任务 ID

若环境未直接给出 `TASK_ID`：从当前 session 活跃 `hermes_tasks` 查询，或用户消息中的 UUID；upsert/begin/finish **前必须确认 taskId**。
