package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.registry.TaskTypeRegistry;
import com.example.aw.gateway.HermesGatewayClient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;

/**
 * 编排：预建任务 + 异步调 Gateway（支持多 taskType）。
 */
@Service
public class CollectSubmitService {

    @Autowired
    private TaskCreateRegistry taskCreateRegistry;

    @Autowired
    private TaskTypeRegistry taskTypeRegistry;

    @Autowired
    private HermesGatewayClient hermesGatewayClient;

    public Map<String, Object> submitCollect(String sessionId, String taskId, String message, String taskType)
            throws Exception {
        return submitInternal(sessionId, taskId, message, taskType, null, false);
    }

    public Map<String, Object> submitCollect(
            String sessionId, String taskId, String message, String taskType, String payloadJson)
            throws Exception {
        return submitInternal(sessionId, taskId, message, taskType, payloadJson, false);
    }

    public Map<String, Object> submitCollectStart(String sessionId, String taskId, String message, String taskType)
            throws Exception {
        return submitCollectStart(sessionId, taskId, message, taskType, null);
    }

    public Map<String, Object> submitCollectStart(
            String sessionId, String taskId, String message, String taskType, String payloadJson)
            throws Exception {
        boolean newConversation = false;
        if (sessionId == null || sessionId.trim().isEmpty()) {
            sessionId = hermesGatewayClient.createSession();
            newConversation = true;
        }
        return submitInternal(sessionId, taskId, message, taskType, payloadJson, newConversation);
    }

    private Map<String, Object> submitInternal(
            String sessionId,
            String taskId,
            String message,
            String taskType,
            String payloadJson,
            boolean newConversation)
            throws Exception {
        if (message == null || message.trim().isEmpty()) {
            throw new IllegalArgumentException("message 不能为空");
        }
        TaskTypeRegistry.TaskTypeDef typeDef = taskTypeRegistry.resolve(taskType);
        String gatewayMessage = taskTypeRegistry.buildGatewayMessage(taskType, message);

        if (taskId == null || taskId.trim().isEmpty()) {
            taskId = UUID.randomUUID().toString();
        }

        taskCreateRegistry.resolve(typeDef.getFrontendType())
                .createPendingTask(taskId, sessionId, gatewayMessage, payloadJson);

        hermesGatewayClient.submitCollectAsync(sessionId, taskId, gatewayMessage);
        return buildAcceptedBody(sessionId, taskId, typeDef, newConversation);
    }

    private Map<String, Object> buildAcceptedBody(
            String sessionId, String taskId, TaskTypeRegistry.TaskTypeDef typeDef, boolean newConversation) {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("sessionId", sessionId);
        body.put("taskId", taskId);
        body.put("taskType", typeDef.getFrontendType());
        body.put("dbTaskType", typeDef.getDbTaskType());
        body.put("skillName", typeDef.getSkillName());
        body.put("newConversation", newConversation);
        body.put("status", "pending");
        body.put("pollTreeUrl", "/api/tasks/" + taskId + "/tree");
        body.put("pollTaskUrl", "/api/tasks/" + taskId);
        String thoughtsUrl = "/api/tasks/" + taskId + "/thoughts/stream";
        body.put("thoughtsStreamUrl", thoughtsUrl);
        // 思考流中继凭证（前端连 Java，不直连 Gateway）
        Map<String, Object> stream = new LinkedHashMap<String, Object>();
        stream.put("mode", "java_relay");
        stream.put("url", thoughtsUrl);
        stream.put("thoughtsUrl", thoughtsUrl);
        boolean reportTask = "account_report".equals(typeDef.getDbTaskType());
        stream.put("enabled", Boolean.valueOf(reportTask));
        Map<String, Object> headers = new LinkedHashMap<String, Object>();
        headers.put("Accept", "text/event-stream");
        stream.put("headers", headers);
        body.put("stream", stream);
        return body;
    }

    /**
     * 只取前端单独传入的 JSON 字段（payload / payloadJson / extra），不组装整份请求体。
     * 值为 Map/List 时序列化；已是字符串则原样入库。
     */
    public static String toPayloadJson(Map<String, Object> req) {
        if (req == null || req.isEmpty()) {
            return null;
        }
        Object raw = req.get("payload");
        if (raw == null) {
            raw = req.get("payloadJson");
        }
        if (raw == null) {
            raw = req.get("extra");
        }
        if (raw == null) {
            return null;
        }
        if (raw instanceof String) {
            String text = ((String) raw).trim();
            return text.isEmpty() ? null : text;
        }
        return JSON.toJSONString(raw);
    }
}
