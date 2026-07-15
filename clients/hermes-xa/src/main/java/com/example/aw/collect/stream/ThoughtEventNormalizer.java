package com.example.aw.collect.stream;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 将 Gateway SSE 原始 data 规范为前端统一结构。
 */
public final class ThoughtEventNormalizer {

    private ThoughtEventNormalizer() {
    }

    public static boolean isTerminal(String gatewayEvent) {
        if (gatewayEvent == null) {
            return false;
        }
        String e = gatewayEvent.trim();
        return "done".equals(e)
                || "run.completed".equals(e)
                || "run.failed".equals(e)
                || "error".equals(e);
    }

    /**
     * @return 含 eventType / content / toolName / success 等字段（不含 seq，由 Hub 分配）
     */
    public static Map<String, Object> normalize(String taskId, String gatewayEvent, String dataJson) {
        String eventType = gatewayEvent == null || gatewayEvent.trim().isEmpty()
                ? "message"
                : gatewayEvent.trim();

        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("taskId", taskId);
        out.put("eventType", eventType);
        out.put("content", null);
        out.put("toolName", null);
        out.put("success", null);

        JSONObject obj = tryParse(dataJson);
        if (obj == null) {
            if (dataJson != null && dataJson.trim().length() > 0) {
                out.put("content", dataJson);
            }
            return out;
        }

        String content = firstString(obj, "delta", "text", "content", "output", "message", "chunk");
        String toolName = firstString(obj, "tool_name", "toolName", "name", "tool");
        Boolean success = firstBoolean(obj, "success", "ok", "passed");

        if ("assistant.delta".equals(eventType) || "reasoning.delta".equals(eventType)) {
            out.put("content", content == null ? "" : content);
        } else if (eventType.startsWith("tool.")) {
            out.put("toolName", toolName);
            out.put("content", content);
            out.put("success", success);
        } else if ("run.completed".equals(eventType) || "assistant.completed".equals(eventType)) {
            out.put("content", content);
        } else {
            if (content != null) {
                out.put("content", content);
            }
            if (toolName != null) {
                out.put("toolName", toolName);
            }
            if (success != null) {
                out.put("success", success);
            }
        }
        return out;
    }

    private static JSONObject tryParse(String dataJson) {
        if (dataJson == null) {
            return null;
        }
        String text = dataJson.trim();
        if (text.isEmpty() || !text.startsWith("{")) {
            return null;
        }
        try {
            return JSON.parseObject(text);
        } catch (Exception e) {
            return null;
        }
    }

    private static String firstString(JSONObject obj, String... keys) {
        for (String key : keys) {
            if (!obj.containsKey(key) || obj.get(key) == null) {
                continue;
            }
            Object val = obj.get(key);
            if (val instanceof String) {
                return (String) val;
            }
            if (val instanceof Number || val instanceof Boolean) {
                return String.valueOf(val);
            }
        }
        return null;
    }

    private static Boolean firstBoolean(JSONObject obj, String... keys) {
        for (String key : keys) {
            if (!obj.containsKey(key) || obj.get(key) == null) {
                continue;
            }
            Object val = obj.get(key);
            if (val instanceof Boolean) {
                return (Boolean) val;
            }
            if (val instanceof Number) {
                return ((Number) val).intValue() != 0;
            }
            if (val instanceof String) {
                String s = ((String) val).trim().toLowerCase();
                if ("true".equals(s) || "1".equals(s) || "ok".equals(s) || "success".equals(s)) {
                    return Boolean.TRUE;
                }
                if ("false".equals(s) || "0".equals(s) || "fail".equals(s) || "failed".equals(s)) {
                    return Boolean.FALSE;
                }
            }
        }
        return null;
    }
}
