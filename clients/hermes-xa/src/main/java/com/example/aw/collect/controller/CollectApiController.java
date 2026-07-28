package com.example.aw.collect.controller;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.registry.TaskTypeRegistry;
import com.example.aw.collect.service.CollectSubmitService;
import com.example.aw.collect.service.TaskEndService;
import com.example.aw.collect.service.TaskFinalAnswerQueryService;
import com.example.aw.collect.service.TaskStepDataQueryService;
import com.example.aw.collect.service.TaskStepToolsQueryService;
import com.example.aw.collect.service.TaskTreeQueryService;
import com.example.aw.collect.service.ThoughtQueryService;
import com.example.aw.collect.stream.ThoughtStreamHub;
import com.example.aw.gateway.HermesGatewayClient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 账号采集 REST API（替代 scripts/demo_collect_api.py）。
 */
@RestController
@RequestMapping("/api")
public class CollectApiController {

    @Autowired
    private HermesGatewayClient hermesGatewayClient;

    @Autowired
    private CollectSubmitService collectSubmitService;

    @Autowired
    private TaskTreeQueryService taskTreeQueryService;

    @Autowired
    private TaskStepDataQueryService taskStepDataQueryService;

    @Autowired
    private TaskStepToolsQueryService taskStepToolsQueryService;

    @Autowired
    private TaskFinalAnswerQueryService taskFinalAnswerQueryService;

    @Autowired
    private ThoughtQueryService thoughtQueryService;

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Autowired
    private ThoughtStreamHub thoughtStreamHub;

    @Autowired
    private TaskTypeRegistry taskTypeRegistry;

    @Autowired
    private TaskEndService taskEndService;

    /**
     * 新对话：向 Hermes 申请 session_id。
     */
    @PostMapping("/conversations")
    public ResponseEntity<Map<String, Object>> createConversation() {
        try {
            String sessionId = hermesGatewayClient.createSession();
            Map<String, Object> body = new LinkedHashMap<String, Object>();
            body.put("sessionId", sessionId);
            return ResponseEntity.status(HttpStatus.CREATED).body(body);
        } catch (Exception e) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "gateway_create_session_failed");
            err.put("detail", e.getMessage());
            return ResponseEntity.status(HttpStatus.BAD_GATEWAY).body(err);
        }
    }

    /**
     * 发起一次采集：立即返回 taskId，Gateway 在后台跑（须已有 sessionId）。
     */
    @PostMapping("/collect")
    public ResponseEntity<Map<String, Object>> collect(@RequestBody Map<String, Object> req) {
        String sessionId = firstNonBlank(req, "sessionId", "session_id");
        String message = firstNonBlank(req, "message", "input");
        String taskId = firstNonBlank(req, "taskId", "task_id");

        if (sessionId.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "sessionId_required");
            err.put("hint", "第一轮新对话请用 POST /api/collect/start，无需传 sessionId");
            return ResponseEntity.badRequest().body(err);
        }
        return doSubmit(
                sessionId,
                taskId,
                message,
                firstNonBlank(req, "taskType", "task_type"),
                CollectSubmitService.toPayloadJson(req));
    }

    /**
     * 【前端主入口】用户发话下任务：无 sessionId 则自动创建会话（封装 Gateway，前端不直连 8642）。
     * 额外字段 payload（或 payloadJson / extra）原样写入 hermes_user_dialogues.payload_json。
     */
    @PostMapping("/collect/start")
    public ResponseEntity<Map<String, Object>> collectStart(@RequestBody Map<String, Object> req) {
        String sessionId = firstNonBlank(req, "sessionId", "session_id");
        String message = firstNonBlank(req, "message", "input");
        String taskId = firstNonBlank(req, "taskId", "task_id");

        if (message.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "message_required");
            return ResponseEntity.badRequest().body(err);
        }
        String taskType = firstNonBlank(req, "taskType", "task_type");
        if (taskType.isEmpty()) {
            taskType = "collect";
        }
        String payloadJson = CollectSubmitService.toPayloadJson(req);
        try {
            Map<String, Object> body = collectSubmitService.submitCollectStart(
                    sessionId.isEmpty() ? null : sessionId,
                    taskId.isEmpty() ? null : taskId,
                    message,
                    taskType,
                    payloadJson);
            return ResponseEntity.status(HttpStatus.ACCEPTED).body(body);
        } catch (IllegalStateException e) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", e.getMessage());
            return ResponseEntity.status(HttpStatus.CONFLICT).body(err);
        } catch (IllegalArgumentException e) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", e.getMessage());
            return ResponseEntity.badRequest().body(err);
        } catch (Exception e) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "collect_submit_failed");
            err.put("detail", e.getMessage());
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(err);
        }
    }

    private ResponseEntity<Map<String, Object>> doSubmit(
            String sessionId, String taskId, String message, String taskType, String payloadJson) {
        if (message.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "message_required");
            return ResponseEntity.badRequest().body(err);
        }
        if (taskType == null || taskType.trim().isEmpty()) {
            taskType = "collect";
        }
        try {
            Map<String, Object> body = collectSubmitService.submitCollect(
                    sessionId, taskId.isEmpty() ? null : taskId, message, taskType, payloadJson);
            return ResponseEntity.status(HttpStatus.ACCEPTED).body(body);
        } catch (IllegalStateException e) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", e.getMessage());
            return ResponseEntity.status(HttpStatus.CONFLICT).body(err);
        } catch (IllegalArgumentException e) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", e.getMessage());
            return ResponseEntity.badRequest().body(err);
        } catch (Exception e) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "collect_submit_failed");
            err.put("detail", e.getMessage());
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(err);
        }
    }

    /**
     * 任务概要。
     */
    @GetMapping("/tasks/{taskId}")
    public ResponseEntity<Map<String, Object>> getTask(@PathVariable String taskId) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "task_not_found");
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(err);
        }
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("taskId", task.get("task_id"));
        body.put("taskType", task.get("task_type"));
        body.put("sessionId", task.get("session_id"));
        body.put("status", task.get("status"));
        body.put("currentPhase", task.get("current_phase"));
        body.put("crossPlatform", task.get("cross_platform"));
        body.put("startedAt", task.get("started_at"));
        body.put("finishedAt", task.get("finished_at"));
        body.put("treeUrl", "/api/tasks/" + taskId + "/tree");
        body.put("thoughtsUrl", "/api/tasks/" + taskId + "/thoughts");
        body.put("thoughtsStreamUrl", "/api/tasks/" + taskId + "/thoughts/stream");
        return ResponseEntity.ok(body);
    }

    /**
     * 用户结束任务：pending/running → cancelled；步骤树不动。
     * 同时将 hermes_user_dialogues 非 summary 的 assistant msg_type 标为 cancelled
     * （无 assistant 则插入一条；不改 user_input / summary）。
     * <p>
     * Agent 由 Gateway 读流发现 cancelled 后断 SSE（与 failed 同路径）。
     * 不杀视频等后台子进程。
     * <p>
     * 200：ok/taskId/status/statusLabel/dialogueMsgType；404 不存在；409 状态不可结束。
     */
    @PostMapping("/tasks/{taskId}/end")
    public ResponseEntity<?> endTask(@PathVariable String taskId) {
        try {
            return ResponseEntity.ok(taskEndService.endTask(taskId));
        } catch (ResponseStatusException e) {
            HttpStatus status = e.getStatus();
            String reason = e.getReason() == null ? status.getReasonPhrase() : e.getReason();
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", reason);
            err.put("detail", reason);
            return ResponseEntity.status(status).body(err);
        } catch (Exception e) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "end_task_failed");
            err.put("detail", e.getMessage());
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(err);
        }
    }

    /**
     * 思考过程查询：完成态返回 assistant.completed 终稿；进行中 mode=live。
     * 另返回 thoughtsTimelineUrl / lastThinkingSeq，历史回放请用 timeline。
     */
    @GetMapping("/tasks/{taskId}/thoughts")
    public ResponseEntity<Map<String, Object>> getThoughts(@PathVariable String taskId) {
        Map<String, Object> body = thoughtQueryService.getThoughts(taskId);
        if (body.containsKey("error") && "task_not_found".equals(body.get("error"))) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(body);
        }
        return ResponseEntity.ok(body);
    }

    /**
     * 思考流可回放时间线：仅 tool.progress + toolName=_thinking 整句。
     * <p>
     * 实时前端仍拼 SSE 的 assistant.delta；历史/刷新用本接口渲染 _thinking。
     * 续传：记下 lastSeq 后再连 SSE {@code /thoughts/stream?afterSeq=}。
     */
    @GetMapping("/tasks/{taskId}/thoughts/timeline")
    public ResponseEntity<Map<String, Object>> getThoughtsTimeline(
            @PathVariable String taskId,
            @RequestParam(value = "afterSeq", defaultValue = "0") long afterSeq) {
        Map<String, Object> body = thoughtQueryService.getTimeline(taskId, afterSeq);
        if (body.containsKey("error") && "task_not_found".equals(body.get("error"))) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(body);
        }
        return ResponseEntity.ok(body);
    }

    /**
     * 系统管线进度注入思考流（Python Hook 在系统采发文/图片/社工库时调用）。
     * <p>
     * 同时推送 assistant.delta（实时碎字区）与 tool.progress+_thinking（timeline 回放），
     * 与 ReportPlanBootstrap 规划文案同一通道，保证步骤树与思考流同阶段可见。
     */
    @PostMapping("/tasks/{taskId}/thoughts/progress")
    public ResponseEntity<Map<String, Object>> postThoughtsProgress(
            @PathVariable String taskId,
            @RequestBody Map<String, Object> body) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "task_not_found");
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(err);
        }
        String content = body == null || body.get("content") == null
                ? ""
                : String.valueOf(body.get("content")).trim();
        if (content.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "empty_content");
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(err);
        }
        if (content.length() > 2000) {
            content = content.substring(0, 2000);
        }
        thoughtStreamHub.open(taskId);
        Map<String, Object> delta = new LinkedHashMap<String, Object>();
        delta.put("taskId", taskId);
        delta.put("content", content);
        thoughtStreamHub.publish(taskId, "assistant.delta", delta);
        Map<String, Object> progress = new LinkedHashMap<String, Object>();
        progress.put("taskId", taskId);
        progress.put("content", content);
        progress.put("toolName", "_thinking");
        thoughtStreamHub.publish(taskId, "tool.progress", progress);
        Map<String, Object> ok = new LinkedHashMap<String, Object>();
        ok.put("ok", Boolean.TRUE);
        ok.put("taskId", taskId);
        return ResponseEntity.ok(ok);
    }

    /**
     * 模型思考过程 SSE 中继（Java 转发 Gateway 的 assistant.delta / tool.* 等）。
     * <p>
     * 前端实时：只拼 assistant.delta.content。
     * 可选 afterSeq：先重放库中 seq&gt;afterSeq 的 _thinking，再接直播（按 seq 去重）。
     */
    @GetMapping(value = "/tasks/{taskId}/thoughts/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter thoughtStream(
            @PathVariable String taskId,
            @RequestParam(value = "afterSeq", required = false, defaultValue = "0") long afterSeq) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "task_not_found");
        }
        return thoughtStreamHub.subscribe(taskId, ThoughtStreamHub.DEFAULT_TIMEOUT_MS, afterSeq);
    }

    /**
     * 密塔式进度树（前端主轮询接口）。
     */
    @GetMapping("/tasks/{taskId}/tree")
    public ResponseEntity<Map<String, Object>> getTaskTree(@PathVariable String taskId) {
        Map<String, Object> tree = taskTreeQueryService.buildTaskTree(taskId);
        if (tree.containsKey("error") && "task_not_found".equals(tree.get("error"))) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(tree);
        }
        return ResponseEntity.ok(tree);
    }

    /**
     * 某步骤对应的业务展示数据（records 为统一 [{label,value}] 结构）。
     */
    @GetMapping("/tasks/{taskId}/steps/{stepKey}/data")
    public ResponseEntity<Map<String, Object>> getStepData(
            @PathVariable String taskId,
            @PathVariable String stepKey) {
        Map<String, Object> data = taskStepDataQueryService.getStepData(taskId, stepKey);
        if (data.containsKey("error")) {
            String err = String.valueOf(data.get("error"));
            if ("task_not_found".equals(err) || "step_not_found".equals(err)) {
                return ResponseEntity.status(HttpStatus.NOT_FOUND).body(data);
            }
        }
        return ResponseEntity.ok(data);
    }

    /**
     * 某步骤下成功的工具调用列表（hermes_tool_outputs，phase 即 stepKey）。
     */
    @GetMapping("/tasks/{taskId}/steps/{stepKey}/tools")
    public ResponseEntity<Map<String, Object>> getStepTools(
            @PathVariable String taskId,
            @PathVariable String stepKey) {
        Map<String, Object> data = taskStepToolsQueryService.listSuccessTools(taskId, stepKey);
        if (data.containsKey("error") && "task_not_found".equals(data.get("error"))) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(data);
        }
        return ResponseEntity.ok(data);
    }

    /**
     * 模型最终回答全文（优先 summary 三节报告）。
     */
    @GetMapping("/tasks/{taskId}/final-answer")
    public ResponseEntity<Map<String, Object>> getFinalAnswer(@PathVariable String taskId) {
        Map<String, Object> answer = taskFinalAnswerQueryService.getFinalAnswer(taskId);
        if (answer.containsKey("error") && "task_not_found".equals(answer.get("error"))) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(answer);
        }
        return ResponseEntity.ok(answer);
    }

    /**
     * 单个节点详情。
     */
    @GetMapping("/tasks/{taskId}/nodes/{nodeId}")
    public ResponseEntity<Map<String, Object>> getNodeDetail(
            @PathVariable String taskId,
            @PathVariable String nodeId) {
        Map<String, Object> detail = taskTreeQueryService.getNodeDetail(taskId, nodeId);
        if (detail.containsKey("error")) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(detail);
        }
        return ResponseEntity.ok(detail);
    }

    /**
     * 同一会话下历史采集任务列表。
     */
    @GetMapping("/conversations/{sessionId}/tasks")
    public ResponseEntity<Map<String, Object>> listSessionTasks(@PathVariable String sessionId) {
        List<Map<String, Object>> rows = collectTaskMapper.selectTasksBySessionId(sessionId);
        List<Map<String, Object>> tasks = new ArrayList<Map<String, Object>>();
        if (rows != null) {
            for (Map<String, Object> row : rows) {
                Map<String, Object> item = new LinkedHashMap<String, Object>();
                Object tid = row.get("task_id");
                item.put("taskId", tid);
                item.put("taskType", taskTypeRegistry.labelOfDbTaskType(stringVal(row.get("task_type"))));
                item.put("status", row.get("status"));
                item.put("createdAt", row.get("created_at"));
                item.put("treeUrl", "/api/tasks/" + tid + "/tree");
                tasks.add(item);
            }
        }
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("sessionId", sessionId);
        body.put("tasks", tasks);
        return ResponseEntity.ok(body);
    }

    /**
     * 历史对话业务类型枚举（含条数，供前端 Tab）。
     */
    @GetMapping("/dialogues/types")
    public ResponseEntity<Map<String, Object>> listDialogueTypes() {
        Map<String, Long> countByDb = new HashMap<String, Long>();
        List<Map<String, Object>> groups = collectTaskMapper.countDialoguesGroupByTaskType();
        if (groups != null) {
            for (Map<String, Object> row : groups) {
                String dbType = stringVal(row.get("task_type"));
                Object cntObj = row.get("cnt");
                long cnt = 0L;
                if (cntObj instanceof Number) {
                    cnt = ((Number) cntObj).longValue();
                }
                if (!dbType.isEmpty()) {
                    countByDb.put(dbType, Long.valueOf(cnt));
                }
            }
        }
        List<Map<String, Object>> types = new ArrayList<Map<String, Object>>();
        for (TaskTypeRegistry.TaskTypeDef def : taskTypeRegistry.listPublicTypes()) {
            Map<String, Object> item = new LinkedHashMap<String, Object>();
            item.put("code", def.getFrontendType());
            item.put("taskType", def.getLabel());
            long cnt = 0L;
            Long found = countByDb.get(def.getDbTaskType());
            if (found != null) {
                cnt = found.longValue();
            }
            item.put("count", Long.valueOf(cnt));
            types.add(item);
        }
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("types", types);
        return ResponseEntity.ok(body);
    }

    /**
     * 历史对话分页查询。
     * 每个 taskId 只返回一条，且仅考虑 msg_type ∈ {user_input, summary, cancelled}；
     * 优先级：cancelled &gt; summary &gt; user_input；
     * payload 一律取同任务 user_input.payload_json。
     *
     * @param taskType 可选，前端短码 collect/expand/verify/report，或库内 account_*
     */
    @GetMapping("/dialogues")
    public ResponseEntity<Map<String, Object>> listDialogues(
            @RequestParam(value = "page", defaultValue = "1") int page,
            @RequestParam(value = "pageSize", defaultValue = "20") int pageSize,
            @RequestParam(value = "taskType", required = false) String taskType) {
        if (page < 1) {
            page = 1;
        }
        if (pageSize < 1) {
            pageSize = 20;
        }
        if (pageSize > 200) {
            pageSize = 200;
        }
        String dbTaskType = null;
        String filterLabel = null;
        if (taskType != null && !taskType.trim().isEmpty()) {
            TaskTypeRegistry.TaskTypeDef def = taskTypeRegistry.resolveExact(taskType);
            if (def == null) {
                Map<String, Object> err = new LinkedHashMap<String, Object>();
                err.put("error", "invalid_taskType");
                err.put("hint", "支持 collect/expand/verify/report 或 account_*");
                return ResponseEntity.badRequest().body(err);
            }
            dbTaskType = def.getDbTaskType();
            filterLabel = def.getLabel();
        }
        long total = collectTaskMapper.countAllDialogues(dbTaskType);
        int offset = (page - 1) * pageSize;
        List<Map<String, Object>> rows = collectTaskMapper.selectDialoguesPage(offset, pageSize, dbTaskType);
        List<Map<String, Object>> dialogues = toDialogueItems(rows);

        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("page", page);
        body.put("pageSize", pageSize);
        body.put("total", Long.valueOf(total));
        if (filterLabel != null) {
            body.put("taskType", filterLabel);
        }
        body.put("list", dialogues);
        return ResponseEntity.ok(body);
    }

    /**
     * 将库行转为前端驼峰结构；payload_json 解析为对象原样返回。
     * taskType 对外返回中文业务名。
     */
    private List<Map<String, Object>> toDialogueItems(List<Map<String, Object>> rows) {
        List<Map<String, Object>> dialogues = new ArrayList<Map<String, Object>>();
        if (rows == null) {
            return dialogues;
        }
        for (Map<String, Object> row : rows) {
            Map<String, Object> item = new LinkedHashMap<String, Object>();
            item.put("id", row.get("id"));
            item.put("taskId", row.get("task_id"));
            item.put("sessionId", row.get("session_id"));
            item.put("role", row.get("role"));
            item.put("content", row.get("content"));
            item.put("msgType", stringVal(row.get("msg_type")));
            String dbType = stringVal(row.get("task_type"));
            item.put("taskType", taskTypeRegistry.labelOfDbTaskType(dbType));
            item.put("payload", parsePayloadJson(row.get("payload_json")));
            item.put("createdAt", row.get("created_at"));
            dialogues.add(item);
        }
        return dialogues;
    }

    private static String stringVal(Object v) {
        return v == null ? "" : String.valueOf(v).trim();
    }

    private Object parsePayloadJson(Object raw) {
        if (raw == null) {
            return null;
        }
        if (raw instanceof Map || raw instanceof List) {
            return raw;
        }
        String text = String.valueOf(raw).trim();
        if (text.isEmpty() || "null".equalsIgnoreCase(text)) {
            return null;
        }
        try {
            return JSON.parse(text);
        } catch (Exception e) {
            return text;
        }
    }

    /**
     * 从 JSON 请求体取字段，兼容驼峰与下划线命名。
     */
    private String firstNonBlank(Map<String, Object> req, String key1, String key2) {
        Object v1 = req.get(key1);
        if (v1 != null && String.valueOf(v1).trim().length() > 0) {
            return String.valueOf(v1).trim();
        }
        Object v2 = req.get(key2);
        if (v2 != null && String.valueOf(v2).trim().length() > 0) {
            return String.valueOf(v2).trim();
        }
        return "";
    }
}
