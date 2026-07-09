//package com.example.aw.resolver;
//
//
//
//import com.coxautodev.graphql.tools.GraphQLQueryResolver;
//import org.springframework.stereotype.Component;
//import org.springframework.web.bind.annotation.CrossOrigin;
//
//import java.io.BufferedReader;
//import java.io.InputStream;
//import java.io.InputStreamReader;
//import java.io.OutputStream;
//import java.net.HttpURLConnection;
//import java.net.URL;
//import java.nio.charset.StandardCharsets;
//import java.util.UUID;
//import java.util.regex.Matcher;
//import java.util.regex.Pattern;
//
///**
// * Hermes Gateway 测试客户端（JDK8，无第三方依赖）。
// */
//
//
//@Component
//@CrossOrigin
//public class HermesGatewayClientBcak implements GraphQLQueryResolver {
//
//    // 按本地 .env 修改
//    private static final String BASE_URL = "http://127.0.0.1:8642";
//    private static final String API_KEY = "dc9b5db558aa4844d0a29d79deb296b75aba58a670c79ecff3ed922839b2c86b";
//    private static final String TEST_QUESTION =
//            "account-intelligence-collect 采集推特 @whyyoutouzhele，需要跨平台采集";
//
//    /** 最近一次 ask 的 session_id，便于调试 */
//    public String lastSessionId;
//    /** 最近一次 ask 的 task_id，便于 Hook 入库关联 */
//    public String lastTaskId;
//
//    /**
//     * 无参测试：问题写死，创建新会话并流式提问，返回助手完整回复。
//     */
//    public String ask() throws Exception {
//        String baseUrl = BASE_URL.endsWith("/") ? BASE_URL.substring(0, BASE_URL.length() - 1) : BASE_URL;
//        String question = TEST_QUESTION;
//        String taskId = UUID.randomUUID().toString();
//        String sessionId = null;
//        StringBuilder answer = new StringBuilder();
//        StringBuilder sseLog = new StringBuilder();
//
//        // ---------- 健康检查 ----------
//        HttpURLConnection healthConn = (HttpURLConnection) new URL(baseUrl + "/health").openConnection();
//        healthConn.setRequestMethod("GET");
//        healthConn.setConnectTimeout(30 * 1000);
//        healthConn.setReadTimeout(30 * 1000);
//        if (healthConn.getResponseCode() != 200) {
//            throw new RuntimeException("Hermes 未启动，请先执行 hermes gateway");
//        }
//
//        // ---------- 创建 session ----------
//        HttpURLConnection sessionConn = (HttpURLConnection) new URL(baseUrl + "/api/sessions").openConnection();
//        sessionConn.setRequestMethod("POST");
//        sessionConn.setDoOutput(true);
//        sessionConn.setConnectTimeout(30 * 1000);
//        sessionConn.setReadTimeout(30 * 1000);
//        sessionConn.setRequestProperty("Authorization", "Bearer " + API_KEY);
//        sessionConn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
//        byte[] sessionBody = "{}".getBytes(StandardCharsets.UTF_8);
//        sessionConn.setRequestProperty("Content-Length", String.valueOf(sessionBody.length));
//        OutputStream sessionOut = sessionConn.getOutputStream();
//        sessionOut.write(sessionBody);
//        sessionOut.flush();
//        sessionOut.close();
//
//        int sessionCode = sessionConn.getResponseCode();
//        InputStream sessionStream = sessionCode >= 200 && sessionCode < 300
//                ? sessionConn.getInputStream() : sessionConn.getErrorStream();
//        StringBuilder sessionResp = new StringBuilder();
//        BufferedReader sessionReader = new BufferedReader(
//                new InputStreamReader(sessionStream, StandardCharsets.UTF_8));
//        String line;
//        while ((line = sessionReader.readLine()) != null) {
//            sessionResp.append(line);
//        }
//        sessionReader.close();
//        if (sessionCode < 200 || sessionCode >= 300) {
//            throw new RuntimeException("创建 session 失败: " + sessionResp);
//        }
//
//        Matcher idMatcher = Pattern.compile("\"(?:id|session_id)\"\\s*:\\s*\"([^\"]+)\"")
//                .matcher(sessionResp.toString());
//        if (!idMatcher.find()) {
//            throw new RuntimeException("无法解析 session_id: " + sessionResp);
//        }
//        sessionId = idMatcher.group(1);
//
//        // ---------- 发起 chat/stream（SSE） ----------
//        String escapedQuestion = question
//                .replace("\\", "\\\\")
//                .replace("\"", "\\\"")
//                .replace("\n", "\\n")
//                .replace("\r", "\\r");
//        String chatBody = "{\"input\":\"" + escapedQuestion + "\"}";
//        String chatUrl = baseUrl + "/api/sessions/" + sessionId + "/chat/stream";
//
//        HttpURLConnection chatConn = (HttpURLConnection) new URL(chatUrl).openConnection();
//        chatConn.setRequestMethod("POST");
//        chatConn.setDoOutput(true);
//        chatConn.setConnectTimeout(30 * 1000);
//        chatConn.setReadTimeout(600 * 1000);
//        chatConn.setRequestProperty("Authorization", "Bearer " + API_KEY);
//        chatConn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
//        chatConn.setRequestProperty("Accept", "text/event-stream");
//        chatConn.setRequestProperty("X-Hermes-Session-Key", "task:" + taskId);
//        byte[] chatBytes = chatBody.getBytes(StandardCharsets.UTF_8);
//        chatConn.setRequestProperty("Content-Length", String.valueOf(chatBytes.length));
//        OutputStream chatOut = chatConn.getOutputStream();
//        chatOut.write(chatBytes);
//        chatOut.flush();
//        chatOut.close();
//
//        int chatCode = chatConn.getResponseCode();
//        InputStream chatStream = chatCode >= 200 && chatCode < 300
//                ? chatConn.getInputStream() : chatConn.getErrorStream();
//        if (chatStream == null) {
//            throw new RuntimeException("chat/stream 无响应, httpCode=" + chatCode);
//        }
//
//        // ---------- 解析 SSE，拼接 assistant 回复 ----------
//        BufferedReader sseReader = new BufferedReader(
//                new InputStreamReader(chatStream, StandardCharsets.UTF_8));
//        String currentEvent = null;
//        String currentData = null;
//        Pattern fieldPattern = null;
//        Matcher fieldMatcher = null;
//
//        while ((line = sseReader.readLine()) != null) {
//            sseLog.append(line).append('\n');
//
//            if (line.startsWith("event:")) {
//                currentEvent = line.substring(6).trim();
//            } else if (line.startsWith("data:")) {
//                currentData = line.substring(5).trim();
//            } else if (line.isEmpty() && currentData != null) {
//                // 从 data JSON 里抠字符串字段（delta / text / content / output）
//                if ("assistant.delta".equals(currentEvent) || currentEvent == null) {
//                    for (String field : new String[]{"delta", "text", "content"}) {
//                        fieldPattern = Pattern.compile(
//                                "\"" + field + "\"\\s*:\\s*\"((?:\\\\.|[^\"\\\\])*)\"");
//                        fieldMatcher = fieldPattern.matcher(currentData);
//                        if (fieldMatcher.find()) {
//                            answer.append(unescapeJson(fieldMatcher.group(1)));
//                            break;
//                        }
//                    }
//                } else if ("assistant.completed".equals(currentEvent)
//                        || "run.completed".equals(currentEvent)) {
//                    for (String field : new String[]{"content", "output"}) {
//                        fieldPattern = Pattern.compile(
//                                "\"" + field + "\"\\s*:\\s*\"((?:\\\\.|[^\"\\\\])*)\"");
//                        fieldMatcher = fieldPattern.matcher(currentData);
//                        if (fieldMatcher.find()) {
//                            String full = unescapeJson(fieldMatcher.group(1));
//                            if (full != null && full.length() > answer.length()) {
//                                answer.setLength(0);
//                                answer.append(full);
//                            }
//                            break;
//                        }
//                    }
//                }
//                currentEvent = null;
//                currentData = null;
//            }
//        }
//        sseReader.close();
//
//        if (chatCode < 200 || chatCode >= 300) {
//            throw new RuntimeException("chat/stream 失败, httpCode=" + chatCode + "\n" + sseLog);
//        }
//
//        this.lastSessionId = sessionId;
//        this.lastTaskId = taskId;
//        return answer.toString();
//    }
//
//    private static String unescapeJson(String value) {
//        if (value == null) {
//            return null;
//        }
//        return value
//                .replace("\\n", "\n")
//                .replace("\\r", "\r")
//                .replace("\\t", "\t")
//                .replace("\\\"", "\"")
//                .replace("\\\\", "\\");
//    }
//
////    public static void main(String[] args) {
////        HermesGatewayClient client = new HermesGatewayClient();
////        try {
////            System.out.println("问题: " + TEST_QUESTION);
////            String reply = client.ask();
////            System.out.println("session_id: " + client.lastSessionId);
////            System.out.println("task_id: " + client.lastTaskId);
////            System.out.println("---------- 回复 ----------");
////            System.out.println(reply);
////        } catch (Exception e) {
////            System.err.println("失败: " + e.getMessage());
////            e.printStackTrace();
////        }
////    }
//}
