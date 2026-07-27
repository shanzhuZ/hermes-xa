package com.example.aw.collect.stream;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Consumer;

/**
 * 思考流中继：Gateway SSE → 内存总线 → 前端 SseEmitter。
 * <p>
 * 旁路：assistant.completed 落库；tool.* 粗同步步骤状态；
 * tool.progress+_thinking 精简事件落库（供历史/刷新回放，失败不影响主流）。
 */
@Component
public class ThoughtStreamHub {

    private static final Logger log = LoggerFactory.getLogger(ThoughtStreamHub.class);

    /** 每个任务保留最近事件，供前端晚连时回放 */
    private static final int BUFFER_LIMIT = 2000;

    /** 前端长连接超时（毫秒） */
    public static final long DEFAULT_TIMEOUT_MS = 2L * 60L * 60L * 1000L;

    /**
     * SSE 实推线程池：publish 只写缓冲后立刻返回，避免 SseEmitter.send 堵住业务线程
     * （曾导致写报 step_plan 永久停在「思考中」）。
     */
    private final ExecutorService pushExecutor = Executors.newCachedThreadPool(
            new java.util.concurrent.ThreadFactory() {
                private final AtomicInteger seq = new AtomicInteger(1);

                @Override
                public Thread newThread(Runnable r) {
                    Thread t = new Thread(r, "thought-sse-push-" + seq.getAndIncrement());
                    t.setDaemon(true);
                    return t;
                }
            });

    private final ConcurrentHashMap<String, TaskChannel> channels = new ConcurrentHashMap<String, TaskChannel>();

    @Autowired
    private ThoughtFinalStore thoughtFinalStore;

    @Autowired
    private CoarseStepSync coarseStepSync;

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Autowired
    private ThoughtTimelineStore thoughtTimelineStore;

    /**
     * 任务开流前调用，避免极早事件无处存放。
     */
    public void open(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        final ExecutorService push = pushExecutor;
        channels.computeIfAbsent(taskId, new java.util.function.Function<String, TaskChannel>() {
            @Override
            public TaskChannel apply(String id) {
                return new TaskChannel(id, push);
            }
        });
    }

    /**
     * 发布一条已规范化事件（会分配 seq 并推给所有订阅者）。
     * 旁路：符合条件的 _thinking 整句落库（失败不影响推送）。
     */
    public void publish(String taskId, String eventType, Map<String, Object> payload) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        final ExecutorService push = pushExecutor;
        TaskChannel channel = channels.computeIfAbsent(taskId, new java.util.function.Function<String, TaskChannel>() {
            @Override
            public TaskChannel apply(String id) {
                return new TaskChannel(id, push);
            }
        });
        Map<String, Object> event = channel.publish(eventType, payload);
        try {
            if (thoughtTimelineStore != null && event != null) {
                thoughtTimelineStore.persistIfThinkingProgress(event);
            }
        } catch (Exception e) {
            log.warn("思考流 timeline 旁路落库失败 taskId={}: {}", taskId, e.getMessage());
        }
    }

    /**
     * 当前是否已有前端订阅该任务思考流。
     */
    public boolean hasSubscribers(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return false;
        }
        TaskChannel channel = channels.get(taskId);
        return channel != null && !channel.subscribers.isEmpty();
    }

    /**
     * 等待至少一个 SSE 订阅者（便于规划事件被前端实时看到）；超时仍返回 false。
     */
    public boolean awaitSubscriber(String taskId, long timeoutMs) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return false;
        }
        open(taskId);
        long deadline = System.currentTimeMillis() + Math.max(0L, timeoutMs);
        while (System.currentTimeMillis() < deadline) {
            if (hasSubscribers(taskId)) {
                return true;
            }
            try {
                Thread.sleep(100L);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return hasSubscribers(taskId);
            }
        }
        return hasSubscribers(taskId);
    }

    /**
     * Gateway 侧流结束（done / 异常）时调用。
     */
    public void complete(String taskId) {
        TaskChannel channel = channels.get(taskId);
        if (channel != null) {
            channel.complete();
        }
    }

    /**
     * 前端订阅；先回放缓冲，再收实时事件。
     */
    public SseEmitter subscribe(String taskId) {
        return subscribe(taskId, DEFAULT_TIMEOUT_MS, 0L);
    }

    public SseEmitter subscribe(String taskId, long timeoutMs) {
        return subscribe(taskId, timeoutMs, 0L);
    }

    /**
     * 前端订阅；可选 afterSeq：先重放库中 seq&gt;afterSeq 的 _thinking，再接内存缓冲与直播（按 seq 去重）。
     */
    public SseEmitter subscribe(String taskId, long timeoutMs, long afterSeq) {
        final ExecutorService push = pushExecutor;
        final TaskChannel channel = channels.computeIfAbsent(taskId, new java.util.function.Function<String, TaskChannel>() {
            @Override
            public TaskChannel apply(String id) {
                return new TaskChannel(id, push);
            }
        });
        final SseEmitter emitter = new SseEmitter(timeoutMs);
        long minSeq = Math.max(0L, afterSeq);
        // 库中已落盘段落（刷新/切换后内存 buffer 可能已丢）
        if (thoughtTimelineStore != null && minSeq >= 0L) {
            try {
                List<Map<String, Object>> fromDb = thoughtTimelineStore.listAfterSeq(taskId, minSeq);
                for (Map<String, Object> ev : fromDb) {
                    if (!TaskChannel.safeSendStatic(emitter, ev)) {
                        return emitter;
                    }
                    Object seqObj = ev.get("seq");
                    if (seqObj instanceof Number) {
                        long s = ((Number) seqObj).longValue();
                        if (s > minSeq) {
                            minSeq = s;
                        }
                    }
                }
            } catch (Exception e) {
                log.warn("订阅前 timeline 重放失败 taskId={}: {}", taskId, e.getMessage());
            }
        }
        channel.attach(emitter, minSeq);
        emitter.onCompletion(new Runnable() {
            @Override
            public void run() {
                channel.detach(emitter);
            }
        });
        emitter.onTimeout(new Runnable() {
            @Override
            public void run() {
                channel.detach(emitter);
                try {
                    emitter.complete();
                } catch (Exception ignore) {
                    // ignore
                }
            }
        });
        emitter.onError(new Consumer<Throwable>() {
            @Override
            public void accept(Throwable throwable) {
                channel.detach(emitter);
            }
        });
        return emitter;
    }

    /**
     * 规范化 Gateway 原始事件后发布；旁路落库终稿与粗同步步骤。
     * <p>
     * 任务已 failed/cancelled：丢弃后续 tool/assistant 事件（stream 不得继续展示自主工具）。
     */
    public void publishGatewayEvent(String taskId, String gatewayEvent, String dataJson) {
        if (isBusinessTaskStopped(taskId) && !ThoughtEventNormalizer.isTerminal(gatewayEvent)
                && !"run.failed".equals(gatewayEvent)
                && !"run.cancelled".equals(gatewayEvent)) {
            log.debug("任务已终止，丢弃 Gateway 事件 taskId={} event={}", taskId, gatewayEvent);
            return;
        }
        Map<String, Object> normalized = ThoughtEventNormalizer.normalize(taskId, gatewayEvent, dataJson);
        String eventType = String.valueOf(normalized.get("eventType"));
        publish(taskId, eventType, normalized);
        try {
            if ("assistant.completed".equals(eventType)) {
                Object content = normalized.get("content");
                if (content != null) {
                    thoughtFinalStore.saveAssistantCompleted(taskId, String.valueOf(content));
                }
            } else if (eventType != null && eventType.startsWith("tool.")) {
                Object toolName = normalized.get("toolName");
                Object successObj = normalized.get("success");
                Boolean success = null;
                if (successObj instanceof Boolean) {
                    success = (Boolean) successObj;
                }
                coarseStepSync.onToolEvent(
                        taskId,
                        eventType,
                        toolName == null ? null : String.valueOf(toolName),
                        success);
            }
        } catch (Exception e) {
            log.warn("思考流旁路处理失败 taskId={} event={}: {}", taskId, eventType, e.getMessage());
        }
        if (ThoughtEventNormalizer.isTerminal(gatewayEvent)) {
            complete(taskId);
        }
    }

    private boolean isBusinessTaskStopped(String taskId) {
        if (taskId == null || taskId.trim().isEmpty() || collectTaskMapper == null) {
            return false;
        }
        try {
            Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
            if (task == null || task.isEmpty()) {
                return false;
            }
            Object st = task.get("status");
            if (st == null) {
                return false;
            }
            String status = String.valueOf(st);
            return "failed".equals(status) || "cancelled".equals(status);
        } catch (Exception e) {
            return false;
        }
    }

    private static final class TaskChannel {
        private final String taskId;
        private final AtomicInteger seq = new AtomicInteger(0);
        private final List<Map<String, Object>> buffer = new ArrayList<Map<String, Object>>();
        private final CopyOnWriteArrayList<SseEmitter> subscribers = new CopyOnWriteArrayList<SseEmitter>();
        private final ExecutorService pushExecutor;
        private volatile boolean completed;

        private TaskChannel(String taskId, ExecutorService pushExecutor) {
            this.taskId = taskId;
            this.pushExecutor = pushExecutor;
        }

        private void attach(SseEmitter emitter) {
            attach(emitter, 0L);
        }

        /**
         * @param afterSeq 仅回放/推送 seq &gt; afterSeq 的事件（与库重放衔接去重）
         */
        private void attach(SseEmitter emitter, long afterSeq) {
            List<Map<String, Object>> snapshot;
            synchronized (buffer) {
                snapshot = new ArrayList<Map<String, Object>>(buffer);
            }
            for (Map<String, Object> event : snapshot) {
                if (eventSeq(event) <= afterSeq) {
                    continue;
                }
                if (!safeSend(emitter, event)) {
                    return;
                }
            }
            if (completed) {
                try {
                    Map<String, Object> end = new LinkedHashMap<String, Object>();
                    end.put("taskId", taskId);
                    end.put("eventType", "stream.end");
                    emitter.send(SseEmitter.event()
                            .name("stream.end")
                            .data(JSON.toJSONString(end), MediaType.APPLICATION_JSON));
                    emitter.complete();
                } catch (Exception e) {
                    try {
                        emitter.completeWithError(e);
                    } catch (Exception ignore) {
                        // ignore
                    }
                }
                return;
            }
            subscribers.add(emitter);
        }

        private void detach(SseEmitter emitter) {
            subscribers.remove(emitter);
        }

        private Map<String, Object> publish(String eventType, Map<String, Object> payload) {
            Map<String, Object> event = new LinkedHashMap<String, Object>();
            if (payload != null) {
                event.putAll(payload);
            }
            event.put("seq", seq.incrementAndGet());
            event.put("taskId", taskId);
            event.put("eventType", eventType);

            synchronized (buffer) {
                buffer.add(event);
                while (buffer.size() > BUFFER_LIMIT) {
                    buffer.remove(0);
                }
            }

            // 快照后异步推：调用方（规划线程/Gateway 转发）不被慢客户端拖死
            final List<SseEmitter> snapshot = new ArrayList<SseEmitter>(subscribers);
            if (!snapshot.isEmpty()) {
                pushExecutor.execute(new Runnable() {
                    @Override
                    public void run() {
                        for (SseEmitter emitter : snapshot) {
                            if (!safeSend(emitter, event)) {
                                subscribers.remove(emitter);
                            }
                        }
                    }
                });
            }
            return event;
        }

        private void complete() {
            if (completed) {
                return;
            }
            completed = true;
            Map<String, Object> end = new LinkedHashMap<String, Object>();
            end.put("taskId", taskId);
            end.put("eventType", "stream.end");
            end.put("seq", seq.incrementAndGet());
            synchronized (buffer) {
                buffer.add(end);
            }
            for (SseEmitter emitter : subscribers) {
                try {
                    emitter.send(SseEmitter.event()
                            .name("stream.end")
                            .data(JSON.toJSONString(end), MediaType.APPLICATION_JSON));
                    emitter.complete();
                } catch (Exception e) {
                    try {
                        emitter.completeWithError(e);
                    } catch (Exception ignore) {
                        // ignore
                    }
                }
            }
            subscribers.clear();
            log.info("思考流结束 taskId={} events={}", taskId, seq.get());
        }

        private boolean safeSend(SseEmitter emitter, Map<String, Object> event) {
            return safeSendStatic(emitter, event);
        }

        private static long eventSeq(Map<String, Object> event) {
            if (event == null) {
                return 0L;
            }
            Object seqObj = event.get("seq");
            if (seqObj instanceof Number) {
                return ((Number) seqObj).longValue();
            }
            try {
                return Long.parseLong(String.valueOf(seqObj));
            } catch (Exception e) {
                return 0L;
            }
        }

        /** 供 Hub 在 attach 前重放 DB 事件 */
        private static boolean safeSendStatic(SseEmitter emitter, Map<String, Object> event) {
            try {
                String type = String.valueOf(event.get("eventType"));
                emitter.send(SseEmitter.event()
                        .name(type)
                        .data(JSON.toJSONString(event), MediaType.APPLICATION_JSON));
                return true;
            } catch (IOException e) {
                log.debug("思考流推送断开: {}", e.getMessage());
                try {
                    emitter.complete();
                } catch (Exception ignore) {
                    // ignore
                }
                return false;
            } catch (Exception e) {
                log.warn("思考流推送失败: {}", e.getMessage());
                try {
                    emitter.completeWithError(e);
                } catch (Exception ignore) {
                    // ignore
                }
                return false;
            }
        }
    }
}
