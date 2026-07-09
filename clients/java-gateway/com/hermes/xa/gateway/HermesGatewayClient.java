package com.hermes.xa.gateway;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Hermes Gateway 薄客户端：只负责会话管理与 chat/stream 调用。
 * <p>
 * 约定：taskId 由 Java/Spring 生成并预写入 MySQL；sessionId 在多轮对话中保持不变；
 * 每次新采集使用新 taskId、复用同一 sessionId。
 */
public class HermesGatewayClient {

    private static final String BASE_URL = "http://127.0.0.1:8642";
    private static final String API_KEY =
            "dc9b5db558aa4844d0a29d79deb296b75aba58a670c79ecff3ed922839b2c86b";

    private final String baseUrl;
    private final String apiKey;
    private final ExecutorService executor = Executors.newCachedThreadPool();

    public HermesGatewayClient() {
        this(BASE_URL, API_KEY);
    }

    public HermesGatewayClient(String baseUrl, String apiKey) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.apiKey = apiKey;
    }

    /** Gateway SSE 事件回调（可选）。 */
    public interface StreamEventListener {
        void onEvent(String eventName, String dataJson);
    }

    /**
     * 新建 Hermes 会话（新对话时调用一次）。
     *
     * @return Gateway 分配的 session_id
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
     * 发起一轮采集对话（异步消费 SSE）。
     * <p>
     * 调用前请由 Spring/MyBatis 执行 create_pending_task 预插 hermes_tasks。
     *
     * @param sessionId  已存在的 Hermes 会话 ID（多轮复用）
     * @param taskId     本次采集业务 ID（每次采集新建）
     * @param userMessage 用户输入
     */
    public void submitCollectAsync(String sessionId, String taskId, String userMessage) throws Exception {
        submitCollectAsync(sessionId, taskId, userMessage, null);
    }

    public void submitCollectAsync(
            String sessionId,
            String taskId,
            String userMessage,
            final StreamEventListener listener) throws Exception {
        final HttpURLConnection conn = openStreamChat(sessionId, taskId, userMessage);
        final int code = conn.getResponseCode();
        if (code < 200 || code >= 300) {
            String err = readAll(conn.getErrorStream());
            throw new RuntimeException("chat/stream 失败, httpCode=" + code + " " + err);
        }
        executor.submit(new Runnable() {
            @Override
            public void run() {
                try {
                    drainStream(conn, listener);
                } catch (Exception e) {
                    e.printStackTrace();
                }
            }
        });
    }

    private HttpURLConnection openStreamChat(String sessionId, String taskId, String userMessage)
            throws Exception {
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

    private static void drainStream(HttpURLConnection conn, StreamEventListener listener) throws Exception {
        InputStream in = conn.getInputStream();
        if (in == null) {
            return;
        }
        BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        String eventName = "";
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.startsWith("event:")) {
                eventName = line.substring(6).trim();
            } else if (line.startsWith("data:") && eventName.length() > 0) {
                String data = line.substring(5).trim();
                if (listener != null) {
                    listener.onEvent(eventName, data);
                }
                if ("done".equals(eventName)) {
                    break;
                }
            }
        }
        reader.close();
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

    /**
     * 演示：建会话 → 生成 taskId → 调 Gateway。
     * 实际 Spring 应在步骤 ③ 前用 MyBatis 预插 hermes_tasks（见 TaskStore.create_pending_task）。
     */
    public static void main(String[] args) throws Exception {
        HermesGatewayClient client = new HermesGatewayClient();
        String sessionId = client.createSession();
        String taskId = UUID.randomUUID().toString();
        String question = "account-intelligence-collect 采集推特 @whyyoutouzhele，需要跨平台采集";
        System.out.println("sessionId=" + sessionId);
        System.out.println("taskId=" + taskId + " （请先用 Spring/demo API 预插 hermes_tasks）");
        client.submitCollectAsync(sessionId, taskId, question, new StreamEventListener() {
            @Override
            public void onEvent(String eventName, String dataJson) {
                System.out.println("[sse] " + eventName + " " + dataJson.substring(0, Math.min(120, dataJson.length())));
            }
        });
        Thread.sleep(500);
        System.out.println("Gateway 请求已发出，SSE 在后台消费。进度请查 GET /api/tasks/" + taskId + "/tree");
    }
}
