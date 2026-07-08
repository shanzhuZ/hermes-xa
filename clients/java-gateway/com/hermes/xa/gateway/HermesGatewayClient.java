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
 * Hermes Gateway 薄客户端：Java 生成 taskId，用其作为 Hermes session_id 建会话后调 chat/stream；
 * MySQL 落库由 collect_01 Hook（pre_llm_call）负责，本类不写库。
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

    /** 生成 taskId 并下任务；Gateway chat/stream 请求成功发出后再返回。 */
    public String submitAsync(String userMessage) throws Exception {
        return submitAsync(UUID.randomUUID().toString(), userMessage);
    }

    /**
     * 使用指定 taskId（与业务库主键一致时传入同一值）。
     * 约定：taskId 同时作为 Hermes session_id，Hook 在 pre_llm_call 时 ensure_task 入库。
     */
    public String submitAsync(String taskId, String userMessage) throws Exception {
        String sessionId = createSession(taskId);
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
                    drainStream(conn);
                } catch (Exception e) {
                    e.printStackTrace();
                }
            }
        });
        return taskId;
    }

    /** 用 taskId 作为 session id，便于 Hook 通过 session_id / extra.task_id 对齐业务主键。 */
    private String createSession(String taskId) throws Exception {
        String body = "{\"id\":\"" + escapeJson(taskId) + "\"}";
        HttpURLConnection conn = (HttpURLConnection) new URL(baseUrl + "/api/sessions").openConnection();
        conn.setRequestMethod("POST");
        conn.setDoOutput(true);
        conn.setConnectTimeout(30 * 1000);
        conn.setReadTimeout(30 * 1000);
        conn.setRequestProperty("Authorization", "Bearer " + apiKey);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        OutputStream out = conn.getOutputStream();
        out.write(bytes);
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

    private static void drainStream(HttpURLConnection conn) throws Exception {
        InputStream in = conn.getInputStream();
        if (in == null) {
            return;
        }
        BufferedReader reader = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8));
        while (reader.readLine() != null) {
            // 后台消费 SSE
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

    public static void main(String[] args) throws Exception {
        String question = "account-intelligence-collect 采集推特 @whyyoutouzhele，需要跨平台采集";
        String taskId = new HermesGatewayClient().submitAsync(question);
        System.out.println("taskId=" + taskId);
    }
}
