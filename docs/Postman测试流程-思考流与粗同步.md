# Postman 测试流程 — 思考流落库 + 步骤粗同步

> **基址**：`http://127.0.0.1:4377`  
> **前置**：已重启 hermes-xa（加载本期代码）；Gateway `8642`、MySQL、Hook 正常。  
> **重点验证**：`GET /thoughts` 终稿、`tool.started` 粗写 running、SSE 只观察 `assistant.delta`。

---

## 0. Postman 准备

1. 新建 Environment，变量：

| 变量 | 初始值 | 说明 |
|------|--------|------|
| `baseUrl` | `http://127.0.0.1:4377` | |
| `sessionId` | （空） | 起任务后写入 |
| `taskId` | （空） | 起任务后写入 |

2. Collection 建议命名：`hermes-xa · thoughts sync`

3. **SSE 说明**：Postman 对 `text/event-stream` 支持一般。  
   - 可以用 Postman 发 GET 看是否持续回包；  
   - 更稳用下方 **步骤 3 的 curl**；  
   - 或浏览器原生 EventSource。  
   不阻塞其它 REST 验收。

---

## 流程总览

```text
① POST 起任务 → 拿 sessionId / taskId
② GET /thoughts（应 mode=live，无 final）
③ 另开 SSE /thoughts/stream（可选）
④ 每 2～3s GET /tree（看粗 running）
⑤ 任务结束后 GET /thoughts（应有 finalContent）
⑥（可选）重启 Java 后再 GET /thoughts（终稿仍在）
```

推荐先测 **短链路**：`taskType: collect` 单平台 Twitter，再测 verify。

---

## ① 发起任务（必测）

**Method**：`POST`  
**URL**：`{{baseUrl}}/api/collect/start`  
**Headers**：

```http
Content-Type: application/json
```

### Body A — 01 采集（推荐先跑这条，更快看到工具）

```json
{
  "taskType": "collect",
  "message": "account-intelligence-collect 采集推特 @laowhy86，单平台即可"
}
```

### Body B — 03 核查（测子步骤粗同步）

```json
{
  "taskType": "verify",
  "message": "account-intelligence-verification 核查账号：推特 @laowhy86"
}
```

**期望**：`202 Accepted`

```json
{
  "sessionId": "...",
  "taskId": "...",
  "thoughtsUrl": "/api/tasks/.../thoughts",
  "thoughtsStreamUrl": "/api/tasks/.../thoughts/stream",
  "stream": { "enabled": true, "mode": "java_relay", ... }
}
```

**Tests 脚本**（自动写环境变量）：

```javascript
pm.test("status 202", function () {
  pm.response.to.have.status(202);
});
var j = pm.response.json();
pm.environment.set("sessionId", j.sessionId);
pm.environment.set("taskId", j.taskId);
pm.test("有 thoughtsUrl", function () {
  pm.expect(j.thoughtsUrl).to.include("/thoughts");
  pm.expect(j.thoughtsStreamUrl).to.include("/thoughts/stream");
  pm.expect(j.stream.enabled).to.eql(true);
});
```

---

## ② 起任务后立刻查思考（I1 进行中）

**Method**：`GET`  
**URL**：`{{baseUrl}}/api/tasks/{{taskId}}/thoughts`

**期望**：`200`

| 字段 | 期望 |
|------|------|
| `mode` | `live` |
| `hasFinal` | `false` |
| `finalContent` | `null` |
| `taskStatus` | `pending` 或 `running` |

**Tests**：

```javascript
pm.test("进行中无终稿", function () {
  var j = pm.response.json();
  pm.expect(j.mode).to.eql("live");
  pm.expect(j.hasFinal).to.eql(false);
});
```

---

## ③ 订阅思考 SSE（可选，建议 curl）

Postman：

```http
GET {{baseUrl}}/api/tasks/{{taskId}}/thoughts/stream
Accept: text/event-stream
```

Settings 里可把 request timeout 调大（如 0 = 无限）。

**更推荐本机 PowerShell / bash：**

```bash
curl -N -H "Accept: text/event-stream" \
  "http://127.0.0.1:4377/api/tasks/<填 taskId>/thoughts/stream"
```

**观察**：

- 会出现 `event: assistant.delta`，data 里有碎字 `content`
- 可能出现 `tool.started` / `tool.completed`（前端渲染应忽略，仅核对有事件）
- 末尾有 `assistant.completed`，之后 `stream.end` / 连接结束

---

## ④ 轮询进度树（验粗同步）

**Method**：`GET`  
**URL**：`{{baseUrl}}/api/tasks/{{taskId}}/tree`

在 Collection Runner 或手动每 2～3 秒点 Send。

**看什么**：

1. 一出现 MCP 工具调用，对应步骤尽快变 `running`（不必等 Hook 长逻辑跑完）  
   - collect：`step1_seed`（twitter info）或 `step6_post_*`  
   - verify：`step3_profile_twitter` / `step3_profile_youtube` 等  
2. Apify 路径：Actor `tool.completed` 后步骤**不应**立刻被标成 `completed`（粗同步禁止）  
3. `status` 变为 `completed` / `failed` 后停止轮询

**Tests（宽松）**：

```javascript
pm.test("tree 有 nodes", function () {
  var j = pm.response.json();
  pm.expect(j.nodes).to.be.an("array");
});
console.log("task status=", pm.response.json().status);
```

---

## ⑤ 任务结束后查思考终稿（必测）

任务 `tree.status` 为 `completed`（或已跑完一轮模型）后：

**Method**：`GET`  
**URL**：`{{baseUrl}}/api/tasks/{{taskId}}/thoughts`

**期望**：

| 字段 | 期望 |
|------|------|
| `hasFinal` | `true` |
| `mode` | `final` |
| `finalContent` | 非空长文本（与 SSE 里 `assistant.completed` 一致或为其覆盖稿） |

**Tests**：

```javascript
pm.test("完成态有终稿", function () {
  var j = pm.response.json();
  pm.expect(j.hasFinal).to.eql(true);
  pm.expect(j.mode).to.eql("final");
  pm.expect(j.finalContent).to.be.a("string").and.not.empty;
});
```

### 对照：业务报告 ≠ 思考终稿

```http
GET {{baseUrl}}/api/tasks/{{taskId}}/final-answer
```

这是 `summary` / 业务报告区；可有可无，**不要**和 `/thoughts` 混为一谈。

---

## ⑥ 重启 Java 后回放（推荐）

1. 重启 hermes-xa 进程（清掉内存 SSE buffer）  
2. 再发：

```http
GET {{baseUrl}}/api/tasks/{{taskId}}/thoughts
```

**期望**：`finalContent` 仍在（证明落库成功，非内存）。

同任务再连 `/thoughts/stream`：同 JVM 无实时缓冲时可能立刻 `stream.end` 或几乎无历史 delta —— **正常**；历史靠 GET。

---

## ⑦ 同会话再开一单（可选）

```http
POST {{baseUrl}}/api/collect/start
Content-Type: application/json

{
  "sessionId": "{{sessionId}}",
  "taskType": "collect",
  "message": "account-intelligence-collect 采集推特 @whyyoutouzhele，单平台即可"
}
```

新 `taskId`，旧任务 `/thoughts` 仍各自独立。

---

## ⑧ SQL 抽查（可选）

```sql
-- 思考终稿
SELECT id, task_id, msg_type, LEFT(content, 200) AS preview, created_at
FROM hermes_user_dialogues
WHERE task_id = '<taskId>' AND msg_type = 'thoughts_final';

-- 步骤状态（粗 + 细）
SELECT step_key, status, message, updated_at
FROM collect_phase_steps
WHERE task_id = '<taskId>'
ORDER BY step_order;
```

---

## 验收对照表

| # | 检查项 | 通过标准 |
|---|--------|----------|
| 1 | 起任务 | 202，返回 `thoughtsUrl` / `stream.enabled=true` |
| 2 | 进行中 GET thoughts | `mode=live`，无 `finalContent` |
| 3 | 工具开跑后 tree | 映射步骤进入 `running` 明显早于业务入库完成 |
| 4 | Apify（若测到） | Actor 完成 ≠ 步骤马上 completed |
| 5 | 结束后 GET thoughts | `mode=final`，`finalContent` 非空 |
| 6 | 重启 Java | 同 taskId 仍能 GET 到终稿 |
| 7 | SSE | 有 `assistant.delta`；终了有 completed（可用 curl） |

---

## 常见失败

| 现象 | 排查 |
|------|------|
| 起任务 502 / gateway 错 | Gateway 8642 是否起来；API Key |
| `/thoughts` 一直 live 无终稿 | 任务是否真出过 `assistant.completed`；看 gateway 日志；Java 是否本版 |
| tree 步骤不早变 running | 工具名是否在粗映射表；是否 verify/collect；子节点是否尚未 insert（可能只更新到父步骤） |
| 404 task_not_found | `taskId` 环境变量未更新 |

---

## Postman Collection 顺序建议

1. `01 start collect`（写变量）  
2. `02 thoughts live`  
3. `03 tree poll`（可 Runner 循环 30 次、间隔 3s，或手动）  
4. `04 thoughts final`（任务结束后再点）  
5. `05 final-answer`（对照报告区，可选）  

SSE 单独用 curl，不必进 Collection Runner。
