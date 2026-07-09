package com.example.aw.collect.service;

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
        return submitInternal(sessionId, taskId, message, taskType, false);
    }

    public Map<String, Object> submitCollectStart(String sessionId, String taskId, String message, String taskType)
            throws Exception {
        boolean newConversation = false;
        if (sessionId == null || sessionId.trim().isEmpty()) {
            sessionId = hermesGatewayClient.createSession();
            newConversation = true;
        }
        return submitInternal(sessionId, taskId, message, taskType, newConversation);
    }

    private Map<String, Object> submitInternal(
            String sessionId, String taskId, String message, String taskType, boolean newConversation)
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
                .createPendingTask(taskId, sessionId, gatewayMessage);

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
        return body;
    }
}
