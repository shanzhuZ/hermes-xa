package com.example.aw.collect.stream;

import com.alibaba.fastjson.JSON;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
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
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Consumer;

/**
 * 思考流中继：Gateway SSE → 内存总线 → 前端 SseEmitter。
 * <p>
 * P0 仅内存；落库回放留给 P1。
 */
@Component
public class ThoughtStreamHub {

    private static final Logger log = LoggerFactory.getLogger(ThoughtStreamHub.class);

    /** 每个任务保留最近事件，供前端晚连时回放 */
    private static final int BUFFER_LIMIT = 2000;

    /** 前端长连接超时（毫秒） */
    public static final long DEFAULT_TIMEOUT_MS = 2L * 60L * 60L * 1000L;

    private final ConcurrentHashMap<String, TaskChannel> channels = new ConcurrentHashMap<String, TaskChannel>();

    /**
     * 任务开流前调用，避免极早事件无处存放。
     */
    public void open(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        channels.computeIfAbsent(taskId, TaskChannel::new);
    }

    /**
     * 发布一条已规范化事件（会分配 seq 并推给所有订阅者）。
     */
    public void publish(String taskId, String eventType, Map<String, Object> payload) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        TaskChannel channel = channels.computeIfAbsent(taskId, TaskChannel::new);
        channel.publish(eventType, payload);
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
        return subscribe(taskId, DEFAULT_TIMEOUT_MS);
    }

    public SseEmitter subscribe(String taskId, long timeoutMs) {
        final TaskChannel channel = channels.computeIfAbsent(taskId, TaskChannel::new);
        final SseEmitter emitter = new SseEmitter(timeoutMs);
        channel.attach(emitter);
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
     * 规范化 Gateway 原始事件后发布。
     */
    public void publishGatewayEvent(String taskId, String gatewayEvent, String dataJson) {
        Map<String, Object> normalized = ThoughtEventNormalizer.normalize(taskId, gatewayEvent, dataJson);
        String eventType = String.valueOf(normalized.get("eventType"));
        publish(taskId, eventType, normalized);
        if (ThoughtEventNormalizer.isTerminal(gatewayEvent)) {
            complete(taskId);
        }
    }

    private static final class TaskChannel {
        private final String taskId;
        private final AtomicInteger seq = new AtomicInteger(0);
        private final List<Map<String, Object>> buffer = new ArrayList<Map<String, Object>>();
        private final CopyOnWriteArrayList<SseEmitter> subscribers = new CopyOnWriteArrayList<SseEmitter>();
        private volatile boolean completed;

        private TaskChannel(String taskId) {
            this.taskId = taskId;
        }

        private void attach(SseEmitter emitter) {
            List<Map<String, Object>> snapshot;
            synchronized (buffer) {
                snapshot = new ArrayList<Map<String, Object>>(buffer);
            }
            for (Map<String, Object> event : snapshot) {
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

        private void publish(String eventType, Map<String, Object> payload) {
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

            for (SseEmitter emitter : subscribers) {
                if (!safeSend(emitter, event)) {
                    subscribers.remove(emitter);
                }
            }
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
            try {
                String type = String.valueOf(event.get("eventType"));
                emitter.send(SseEmitter.event()
                        .name(type)
                        .data(JSON.toJSONString(event), MediaType.APPLICATION_JSON));
                return true;
            } catch (IOException e) {
                log.debug("思考流推送断开 taskId={}: {}", taskId, e.getMessage());
                try {
                    emitter.complete();
                } catch (Exception ignore) {
                    // ignore
                }
                return false;
            } catch (Exception e) {
                log.warn("思考流推送失败 taskId={}: {}", taskId, e.getMessage());
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
