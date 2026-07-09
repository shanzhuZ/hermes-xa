package com.example.aw.collect.service;

import com.example.aw.gateway.HermesGatewayClient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 编排：预建任务 + 异步调 Gateway。
 */
@Service
public class CollectSubmitService {

    @Autowired
    private CollectTaskCreateService collectTaskCreateService;

    @Autowired
    private HermesGatewayClient hermesGatewayClient;

    /**
     * 完整提交一次采集：先写库拿 taskId，再后台调 Hermes。
     *
     * @return 含 taskId、轮询地址等字段的 Map
     */
    public Map<String, Object> submitCollect(String sessionId, String taskId, String message) throws Exception {
        String finalTaskId = collectTaskCreateService.createPendingTask(taskId, sessionId, message);
        hermesGatewayClient.submitCollectAsync(sessionId, finalTaskId, message);
        return buildAcceptedBody(sessionId, finalTaskId, false);
    }

    /**
     * 前端推荐入口：无 sessionId 时自动建会话（第一轮新对话），再下任务。
     *
     * @param sessionId 可为空；空则调 Gateway 新建 session
     * @return 含 sessionId、taskId、是否新会话、轮询 tree 地址
     */
    public Map<String, Object> submitCollectStart(String sessionId, String taskId, String message) throws Exception {
        boolean newConversation = false;
        if (sessionId == null || sessionId.trim().isEmpty()) {
            sessionId = hermesGatewayClient.createSession();
            newConversation = true;
        }
        String finalTaskId = collectTaskCreateService.createPendingTask(taskId, sessionId, message);
        hermesGatewayClient.submitCollectAsync(sessionId, finalTaskId, message);
        return buildAcceptedBody(sessionId, finalTaskId, newConversation);
    }

    /**
     * 组装 202 响应体，前端拿 taskId 轮询 pollTreeUrl 即可。
     */
    private Map<String, Object> buildAcceptedBody(String sessionId, String taskId, boolean newConversation) {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("sessionId", sessionId);
        body.put("taskId", taskId);
        body.put("newConversation", newConversation);
        body.put("status", "pending");
        body.put("pollTreeUrl", "/api/tasks/" + taskId + "/tree");
        body.put("pollTaskUrl", "/api/tasks/" + taskId);
        return body;
    }
}
