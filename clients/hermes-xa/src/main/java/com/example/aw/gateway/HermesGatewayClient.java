package com.example.aw.gateway;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.stream.ThoughtStreamHub;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 调用 Hermes Gateway（建会话、chat/stream），不写业务库。
 * <p>
 * 消费 Gateway SSE 时同步转发到 {@link ThoughtStreamHub}，供前端中继订阅。
 */
@Component
public class HermesGatewayClient {

    private static final Logger log = LoggerFactory.getLogger(HermesGatewayClient.class);

    private final String baseUrl = "http://127.0.0.1:8642";
    private final String apiKey =
            "dc9b5db558aa4844d0a29d79deb296b75aba58a670c79ecff3ed922839b2c86b";

    private final ExecutorService executor = Executors.newCachedThreadPool();

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Autowired
    private ThoughtStreamHub thoughtStreamHub;

    /**
     * 向 Gateway 申请一个新的 Hermes 会话 ID（用户点「新对话」时调用）。
     */
    public String createSession() throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(baseUrl + "/api/sessions").openConnection();
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setConnectTimeout(30 * 1000);
        conn.setReadTimeout(30 * 1000);
        conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        byte[] body = "{}".getBytes(StandardCharsets.UTF_8);
        OutputStream out = conn.getOutputStream();
        out.write(body);
        out.flush();
        out.close();

        int code = conn.getResponseCode();
        String resp = readAll(code < 300 ? conn.getInputStream() : conn.getErrorStream());
        if (code < 200 || code >= 300) {
            throw new RuntimeException("创建 session 失败: " + resp);
        }
        Matcher m = Pattern.compile("\"(?:id|session_id)\"\\s*:\\s*\"([^\"]+)\"").matcher(resp);
        if (!m.find()) {
            throw new RuntimeException("无法解析 session_id: " + resp);
        }
        return m.group(1);
    }

    /**
     * 在已有 session 上发起一轮采集（异步读 SSE，不阻塞 HTTP 接口返回）。
     *
     * @param sessionId   Hermes 会话，多轮复用
     * @param taskId      本次采集业务 ID，调用前须已预插 hermes_tasks
     * @param userMessage 用户输入
     */
    public void submitCollectAsync(final String sessionId, final String taskId, final String userMessage)
            throws Exception {
        thoughtStreamHub.open(taskId);
        Map<String, Object> started = new LinkedHashMap<String, Object>();
        started.put("taskId", taskId);
        started.put("sessionId", sessionId);
        thoughtStreamHub.publish(taskId, "run.started", started);

        final HttpURLConnection conn = openStreamChat(sessionId, taskId, userMessage);
        int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            String err = readAll(conn.getErrorStream());
            Map<String, Object> failed = new LinkedHashMap<String, Object>();
            failed.put("content", err);
            failed.put("success", Boolean.FALSE);
            thoughtStreamHub.publish(taskId, "run.failed", failed);
            thoughtStreamHub.complete(taskId);
            throw new RuntimeException("chat/stream 失败, httpCode=" + code + " " + err);
        }
        executor.submit(new Runnable() {
            @Override
            public void run() {
                try {
                    drainStream(conn, taskId);
                } catch (Exception e) {
                    log.error("消费 Gateway SSE 失败 taskId={}", taskId, e);
                    Map<String, Object> failed = new LinkedHashMap<String, Object>();
                    failed.put("content", truncateErr(e.getMessage()));
                    failed.put("success", Boolean.FALSE);
                    try {
                        thoughtStreamHub.publish(taskId, "run.failed", failed);
                        thoughtStreamHub.complete(taskId);
                    } catch (Exception ignore) {
                        // ignore
                    }
                    collectTaskMapper.markTaskFailed(taskId, truncateErr(e.getMessage()));
                }
            }
        });
    }

    /**
     * 打开 chat/stream 长连接，Header 带上业务 taskId 供长期记忆作用域使用。
     */
    private HttpURLConnection openStreamChat(String sessionId, String taskId, String userMessage) throws Exception {
        String body = "{\"input\":\"" + escapeJson(userMessage) + "\"}";
        HttpURLConnection conn = (HttpURLConnection) new URL(
                baseUrl + "/api/sessions/" + sessionId + "/chat/stream").openConnection();
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setConnectTimeout(30 * 1000);
        conn.setReadTimeout(60 * 60 * 1000);
        conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Accept", "text/event-stream");
        conn.setRequestProperty("X-Hermes-Session-Key", "task:" + taskId);
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        conn.setRequestProperty("Content-Length", String.valueOf(bytes.length));
        OutputStream out = conn.getOutputStream();
        out.write(bytes);
        out.flush();
        out.close();
        return conn;
    }

    /**
     * 后台读完 SSE 事件流；转发到思考中继；遇到 event:done 结束。
     * <p>
     * Hook 将任务标为 failed 后：断开 Gateway SSE（触发 agent.interrupt），
     * 并向中继发 run.failed，避免流程图已 skip 但 stream 仍输出 web_search。
     */
    private void drainStream(HttpURLConnection conn, String taskId) throws Exception {
        InputStream in = conn.getInputStream();
        if (in == null) {
            thoughtStreamHub.complete(taskId);
            return;
        }
        BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        String eventName = "";
        String line;
        int linesSinceStatusCheck = 0;
        boolean abortedForBusinessFail = false;
        try {
            while ((line = reader.readLine()) != null) {
                linesSinceStatusCheck++;
                if (line.startsWith("event:")) {
                    eventName = line.substring(6).trim();
                } else if (line.startsWith("data:") && eventName.length() > 0) {
                    String data = line.substring(5).trim();
                    String logData = data.length() > 200 ? data.substring(0, 200) : data;
                    log.debug("[gateway] task={} event={} data={}", taskId, eventName, logData);
                    thoughtStreamHub.publishGatewayEvent(taskId, eventName, data);
                    // tool.completed 后 Hook 可能稍后才 mark failed：稍等再查；
                    // tool.started 再查一次，拦住失败后的 web_search 等后续工具
                    if ("tool.completed".equals(eventName) || "tool.failed".equals(eventName)) {
                        try {
                            Thread.sleep(400);
                        } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                        }
                    }
                    if ("tool.completed".equals(eventName) || "tool.failed".equals(eventName)
                            || "tool.started".equals(eventName)
                            || linesSinceStatusCheck >= 40) {
                        linesSinceStatusCheck = 0;
                        if (abortStreamIfTaskFailed(conn, taskId)) {
                            abortedForBusinessFail = true;
                            break;
                        }
                    }
                    if ("done".equals(eventName)) {
                        break;
                    }
                    eventName = "";
                } else if (line.isEmpty()) {
                    eventName = "";
                }
            }
        } finally {
            try {
                reader.close();
            } catch (Exception ignore) {
                // ignore
            }
            try {
                conn.disconnect();
            } catch (Exception ignore) {
                // ignore
            }
            if (!abortedForBusinessFail) {
                // publishGatewayEvent 在终端事件时已 complete；此处兜底
                thoughtStreamHub.complete(taskId);
            }
        }
    }

    /**
     * 若 hermes_tasks.status=failed，断开 SSE 并发布 run.failed。
     *
     * @return true 表示已中止读流
     */
    private boolean abortStreamIfTaskFailed(HttpURLConnection conn, String taskId) {
        try {
            Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
            if (task == null || task.isEmpty()) {
                return false;
            }
            Object st = task.get("status");
            if (st == null || !"failed".equals(String.valueOf(st))) {
                return false;
            }
            Object err = task.get("error_message");
            String msg = err == null || String.valueOf(err).trim().isEmpty()
                    ? "任务已失败，已中止 Agent"
                    : String.valueOf(err).trim();
            Map<String, Object> failed = new LinkedHashMap<String, Object>();
            failed.put("content", truncateErr(msg));
            failed.put("success", Boolean.FALSE);
            failed.put("reason", "business_task_failed");
            thoughtStreamHub.publish(taskId, "run.failed", failed);
            thoughtStreamHub.complete(taskId);
            try {
                conn.disconnect();
            } catch (Exception ignore) {
                // ignore
            }
            log.info("业务任务已 failed，断开 Gateway SSE taskId={}", taskId);
            return true;
        } catch (Exception e) {
            log.warn("检查任务失败状态异常 taskId={}: {}", taskId, e.getMessage());
            return false;
        }
    }

    private static String readAll(InputStream in) throws Exception {
        if (in == null) {
            return "";
        }
        BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        String line;
        while ((line = r.readLine()) != null) {
            sb.append(line);
        }
        r.close();
        return sb.toString();
    }

    private static String escapeJson(String s) {
        if (s == null) {
            return "";
        }
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n").replace("\r", "\\r");
    }

    private static String truncateErr(String msg) {
        if (msg == null) {
            return "";
        }
        return msg.length() > 2000 ? msg.substring(0, 2000) : msg;
    }
}
