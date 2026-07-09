package com.example.aw.collect.controller;

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
        return doSubmit(sessionId, taskId, message, firstNonBlank(req, "taskType", "task_type"));
    }

    /**
     * 【前端主入口】用户发话下任务：无 sessionId 则自动创建会话（封装 Gateway，前端不直连 8642）。
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
        try {
            Map<String, Object> body = collectSubmitService.submitCollectStart(
                    sessionId.isEmpty() ? null : sessionId,
                    taskId.isEmpty() ? null : taskId,
                    message,
                    taskType);
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
            String sessionId, String taskId, String message, String taskType) {
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
                    sessionId, taskId.isEmpty() ? null : taskId, message, taskType);
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
