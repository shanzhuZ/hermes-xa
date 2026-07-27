# 思考流可回放约定（timeline + SSE）

> 适用：采集 / 拓线 / 核验 / 写报（凡走 Java `ThoughtStreamHub` 的任务）。  
> 目标：切换任务、刷新、已完成任务能回放思考进度；**不改动** Python Hook / 步骤树执行流。

## 1. 两条渲染路径（不要混）

| 场景 | 数据源 | 前端怎么渲染 |
|------|--------|----------------|
| **实时进行中** | SSE `GET /thoughts/stream` | **只追加** `eventType === "assistant.delta"` 的 `content`（碎字拼接） |
| **历史 / 刷新补齐** | REST `GET /thoughts/timeline` | 渲染 `events[]` 里 `_thinking` 整句（`tool.progress` + `toolName=_thinking`） |

接受观感不同：实时是流式碎字，历史是整句进度。

**不要**再把 `thoughts_final` / `GET /thoughts.finalContent` 当作思考流回放主数据源（可保留兼容，非本方案路径）。

## 2. 落库范围

仅当 SSE 事件同时满足：

- `eventType === "tool.progress"`
- `toolName === "_thinking"`
- `content` 非空

才写入表 `hermes_thought_events`（`seq` 与 SSE 同一单调序号，`content` 最长约 2000 字）。

不落：`assistant.delta`、真实工具起止/大返回。

## 3. 接口

### 3.1 时间线（历史 / 刷新）

```http
GET /api/tasks/{taskId}/thoughts/timeline?afterSeq=0
```

**响应 200 示例：**

```json
{
  "taskId": "df3f5984-4614-4929-b412-0878561fb3e4",
  "taskStatus": "running",
  "afterSeq": 0,
  "lastSeq": 387,
  "events": [
    {
      "taskId": "df3f5984-4614-4929-b412-0878561fb3e4",
      "eventType": "tool.progress",
      "content": "引擎已进入步骤4，待处理2个主页子步骤：YouTube 和 GitHub。",
      "toolName": "_thinking",
      "seq": 387,
      "createdAt": "2026-07-27T14:00:00.000"
    }
  ],
  "thoughtsStreamUrl": "/api/tasks/.../thoughts/stream?afterSeq=387"
}
```

- 已完成 / 已取消 / 进行中均可调（有数据就返回）。
- `afterSeq`：只返回 `seq > afterSeq` 的事件。

### 3.2 SSE（实时 + 可选续传）

```http
GET /api/tasks/{taskId}/thoughts/stream?afterSeq=0
Accept: text/event-stream
```

- 不传或 `afterSeq=0`：行为与旧版兼容（先内存缓冲回放，再直播）。
- 传 `afterSeq=N`：先重放库中 `seq > N` 的 `_thinking`，再接内存缓冲与直播；服务端按 `seq` 去重衔接。

实时区仍建议：**只拼 `assistant.delta`**；`tool.progress` / `_thinking` 可忽略（历史已用 timeline）。

### 3.3 元信息

```http
GET /api/tasks/{taskId}/thoughts
```

新增字段：`lastThinkingSeq`、`thoughtsTimelineUrl`（旧字段保留）。

## 4. 推荐前端流程

### 打开历史已完成任务

1. `GET .../thoughts/timeline` → 用 `events` 渲染思考区（整句列表或覆盖式，按产品习惯）。
2. **不必**再连 SSE（或连了也忽略）。

### 进行中刷新 / 切换回来

1. `GET .../thoughts/timeline` → 先画已落库 `_thinking`。
2. 记下 `lastSeq`。
3. `GET .../thoughts/stream?afterSeq={lastSeq}`。
4. 实时区：只追加 `seq > lastSeq` 的 `assistant.delta`（前端也可再按 `seq` 去重）。

### 首次进入进行中任务（从未看过）

- 可直接连 SSE（`afterSeq=0`），实时拼 delta。
- 或先 timeline 再 SSE，效果等价于「先有整句底、再接碎字」。

## 5. 运维

执行迁移：

```bash
# 按项目既有 schema_migrations 方式执行
scripts/sql/016_hermes_thought_events.sql
```

删除历史任务时会级联删 `hermes_thought_events`（HistoryQa 删除接口已接入）。

## 6. 隔离保证

- 落库在 `ThoughtStreamHub.publish` 旁路；异常只打日志。
- 不进入 Python Hook、不改步骤树细状态、不改 Gateway 主读流。
