package com.example.aw.collect.controller;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.service.CollectSubmitService;
import com.example.aw.collect.service.TaskFinalAnswerQueryService;
import com.example.aw.collect.service.TaskStepDataQueryService;
import com.example.aw.collect.service.TaskStepToolsQueryService;
import com.example.aw.collect.service.TaskTreeQueryService;
import com.example.aw.gateway.HermesGatewayClient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.ArrayList;
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
    private CollectTaskMapper collectTaskMapper;

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
        return ResponseEntity.ok(body);
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
                item.put("taskType", row.get("task_type"));
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
     * 历史对话分页查询（仅分页参数）。
     * 同一 taskId 若已有 assistant，只返回该任务最新一条 assistant；
     * 若该条 payload 为空，则从同任务其它记录（通常为 user）回填 payload。
     */
    @GetMapping("/dialogues")
    public ResponseEntity<Map<String, Object>> listDialogues(
            @RequestParam(value = "page", defaultValue = "1") int page,
            @RequestParam(value = "pageSize", defaultValue = "20") int pageSize) {
        if (page < 1) {
            page = 1;
        }
        if (pageSize < 1) {
            pageSize = 20;
        }
        if (pageSize > 200) {
            pageSize = 200;
        }
        long total = collectTaskMapper.countAllDialogues();
        int offset = (page - 1) * pageSize;
        List<Map<String, Object>> rows = collectTaskMapper.selectDialoguesPage(offset, pageSize);
        List<Map<String, Object>> dialogues = toDialogueItems(rows);

        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("page", page);
        body.put("pageSize", pageSize);
        body.put("total", total);
        body.put("list", dialogues);
        return ResponseEntity.ok(body);
    }

    /**
     * 将库行转为前端驼峰结构；payload_json 解析为对象原样返回。
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
            item.put("msgType", row.get("msg_type"));
            item.put("payload", parsePayloadJson(row.get("payload_json")));
            item.put("createdAt", row.get("created_at"));
            dialogues.add(item);
        }
        return dialogues;
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
