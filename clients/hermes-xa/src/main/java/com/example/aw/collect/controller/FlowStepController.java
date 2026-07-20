package com.example.aw.collect.controller;

import com.example.aw.collect.service.FlowStepService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 动态流程图 REST（主要为第五类 custom / 后续继续问答共用）。
 * <p>
 * 前端进度仍轮询 {@code GET /api/tasks/{taskId}/tree}；本 Controller 负责写步骤。
 * Agent 侧可通过 Java HTTP，或 Python {@code custom_05.flow_cli} 直写 MySQL。
 */
@RestController
@RequestMapping("/api/tasks/{taskId}/flow")
public class FlowStepController {

    @Autowired
    private FlowStepService flowStepService;

    /**
     * 批量 upsert 步骤（merge 追加/更新；replace 以本次列表为准删多余节点）。
     * <pre>
     * POST /api/tasks/{taskId}/flow/upsert
     * { "mode":"merge", "steps":[{"stepKey":"research","title":"检索资料","stepOrder":20}] }
     * </pre>
     */
    @PostMapping("/upsert")
    @SuppressWarnings("unchecked")
    public ResponseEntity<?> upsert(@PathVariable("taskId") String taskId,
                                    @RequestBody Map<String, Object> body) {
        try {
            String mode = body == null ? null : stringVal(body.get("mode"));
            List<Map<String, Object>> steps = null;
            if (body != null && body.get("steps") instanceof List) {
                steps = (List<Map<String, Object>>) body.get("steps");
            }
            return ResponseEntity.ok(flowStepService.upsertSteps(taskId, mode, steps));
        } catch (ResponseStatusException e) {
            return error(e);
        } catch (Exception e) {
            return error(HttpStatus.INTERNAL_SERVER_ERROR, "flow_upsert_failed", e.getMessage());
        }
    }

    /**
     * 开始某步骤：status → running。
     * <pre>
     * POST /api/tasks/{taskId}/flow/begin
     * { "stepKey":"research", "message":"开始检索" }
     * </pre>
     */
    @PostMapping("/begin")
    public ResponseEntity<?> begin(@PathVariable("taskId") String taskId,
                                   @RequestBody Map<String, Object> body) {
        try {
            String stepKey = body == null ? null : first(body, "stepKey", "step_key");
            String message = body == null ? null : stringVal(body.get("message"));
            return ResponseEntity.ok(flowStepService.beginStep(taskId, stepKey, message));
        } catch (ResponseStatusException e) {
            return error(e);
        } catch (Exception e) {
            return error(HttpStatus.INTERNAL_SERVER_ERROR, "flow_begin_failed", e.getMessage());
        }
    }

    /**
     * 结束某步骤：completed / failed / skipped。
     * <pre>
     * POST /api/tasks/{taskId}/flow/finish
     * { "stepKey":"research", "status":"completed", "message":"已汇总 3 条线索" }
     * </pre>
     */
    @PostMapping("/finish")
    public ResponseEntity<?> finish(@PathVariable("taskId") String taskId,
                                    @RequestBody Map<String, Object> body) {
        try {
            String stepKey = body == null ? null : first(body, "stepKey", "step_key");
            String status = body == null ? null : stringVal(body.get("status"));
            String message = body == null ? null : stringVal(body.get("message"));
            return ResponseEntity.ok(flowStepService.finishStep(taskId, stepKey, status, message));
        } catch (ResponseStatusException e) {
            return error(e);
        } catch (Exception e) {
            return error(HttpStatus.INTERNAL_SERVER_ERROR, "flow_finish_failed", e.getMessage());
        }
    }

    private static String first(Map<String, Object> body, String a, String b) {
        String v = stringVal(body.get(a));
        if (!v.isEmpty()) {
            return v;
        }
        return stringVal(body.get(b));
    }

    private static String stringVal(Object v) {
        return v == null ? null : String.valueOf(v);
    }

    private ResponseEntity<Map<String, Object>> error(ResponseStatusException e) {
        HttpStatus status = e.getStatus();
        String reason = e.getReason() == null ? status.getReasonPhrase() : e.getReason();
        return error(status, reason, reason);
    }

    private ResponseEntity<Map<String, Object>> error(HttpStatus status, String error, String detail) {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("error", error);
        body.put("detail", detail);
        return ResponseEntity.status(status).body(body);
    }
}
