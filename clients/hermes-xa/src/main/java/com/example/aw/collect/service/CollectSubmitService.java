package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.registry.TaskTypeRegistry;
import com.example.aw.collect.stream.ThoughtStreamHub;
import com.example.aw.gateway.HermesGatewayClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

/**
 * 编排：预建任务 + 异步调 Gateway（支持多 taskType）。
 */
@Service
public class CollectSubmitService {

    private static final Logger log = LoggerFactory.getLogger(CollectSubmitService.class);

    /**
     * 写报：规划慢推 + 启 Agent。必须用缓存线程池，禁止单线程：
     * 否则 SSE 推送或 Gateway 建连阻塞时，后续任务会永久卡在 step_plan=思考中。
     */
    private final ScheduledExecutorService reportStartScheduler = Executors.newScheduledThreadPool(
            4,
            new java.util.concurrent.ThreadFactory() {
                private final java.util.concurrent.atomic.AtomicInteger seq =
                        new java.util.concurrent.atomic.AtomicInteger(1);

                @Override
                public Thread newThread(Runnable r) {
                    Thread t = new Thread(r, "report-plan-then-agent-" + seq.getAndIncrement());
                    t.setDaemon(true);
                    return t;
                }
            });

    @Autowired
    private TaskCreateRegistry taskCreateRegistry;

    @Autowired
    private TaskTypeRegistry taskTypeRegistry;

    @Autowired
    private HermesGatewayClient hermesGatewayClient;

    @Autowired
    private ReportPlanBootstrap reportPlanBootstrap;

    @Autowired
    private ThoughtStreamHub thoughtStreamHub;

    @Autowired
    private CollectTaskMapper collectTaskMapper;

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

        if ("account_report".equals(typeDef.getDbTaskType())) {
            // 1) 同步只推「思考中…」，立刻返回 taskId 让前端连 SSE
            // 2) 后台慢推规划文案（约数秒）→ 再启 Agent
            reportPlanBootstrap.beginPlanStream(taskId);
            final String sid = sessionId;
            final String tid = taskId;
            final String msg = gatewayMessage;
            reportStartScheduler.schedule(new Runnable() {
                @Override
                public void run() {
                    try {
                        thoughtStreamHub.awaitSubscriber(tid, 2500L);
                    } catch (Exception e) {
                        log.warn("04 等待 SSE 订阅者异常（继续规划） taskId={}: {}", tid, e.getMessage());
                    }
                    try {
                        reportPlanBootstrap.finishPlanStreamPaced(tid);
                    } catch (Exception e) {
                        log.error("04 规划慢推失败，强制收口 step_plan taskId={}", tid, e);
                        forceCompleteStepPlan(tid);
                    }
                    // 兜底：禁止 step_plan 永久停在 running（思考中）
                    forceCompleteStepPlanIfStillRunning(tid);
                    try {
                        hermesGatewayClient.submitCollectAsync(sid, tid, msg);
                    } catch (Exception e) {
                        log.error("04 启动 Agent 失败 taskId={}", tid, e);
                    }
                }
            }, 200L, TimeUnit.MILLISECONDS);
        } else {
            hermesGatewayClient.submitCollectAsync(sessionId, taskId, gatewayMessage);
        }
        return buildAcceptedBody(sessionId, taskId, typeDef, newConversation);
    }

    /** 规划异常时至少让第 0 步离开 running，避免前端永久卡住。 */
    private void forceCompleteStepPlan(String taskId) {
        try {
            collectTaskMapper.updateStepStatus(
                    taskId,
                    ReportTaskCreateService.STEP_PLAN,
                    "completed",
                    "已规划好节点：1.锁定目标；2.线索发现；3.账号采集；4.关联碰撞；"
                            + "5.内容采集；6.深度研判；7.报告生成");
        } catch (Exception e) {
            log.warn("强制收口 step_plan 失败 taskId={}: {}", taskId, e.getMessage());
        }
    }

    private void forceCompleteStepPlanIfStillRunning(String taskId) {
        try {
            String st = collectTaskMapper.selectStepStatus(taskId, ReportTaskCreateService.STEP_PLAN);
            if (st != null && "running".equals(st)) {
                log.warn("step_plan 仍 running，强制 completed taskId={}", taskId);
                forceCompleteStepPlan(taskId);
            }
        } catch (Exception e) {
            log.warn("检查 step_plan 状态失败 taskId={}: {}", taskId, e.getMessage());
            forceCompleteStepPlan(taskId);
        }
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
        String thoughtsReplayUrl = "/api/tasks/" + taskId + "/thoughts";
        String thoughtsStreamUrl = "/api/tasks/" + taskId + "/thoughts/stream";
        body.put("thoughtsUrl", thoughtsReplayUrl);
        body.put("thoughtsStreamUrl", thoughtsStreamUrl);
        // 思考流中继凭证（前端连 Java，不直连 Gateway）
        Map<String, Object> stream = new LinkedHashMap<String, Object>();
        stream.put("mode", "java_relay");
        stream.put("url", thoughtsStreamUrl);
        stream.put("thoughtsUrl", thoughtsReplayUrl);
        stream.put("thoughtsStreamUrl", thoughtsStreamUrl);
        // 四业务共用思考中继
        stream.put("enabled", Boolean.TRUE);
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
