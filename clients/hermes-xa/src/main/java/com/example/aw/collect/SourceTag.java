package com.example.aw.collect;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * collect_phase_steps.source_tag：JSON 列表（付费/开源/离线/自研/特色）。
 * 对照：docs/collect_phase_steps_source_tag.md
 */
public final class SourceTag {

    public static final String PAID = "付费";
    public static final String OPEN = "开源";
    public static final String OFFLINE = "离线";
    public static final String SELF = "自研";
    public static final String FEATURE = "特色";

    private static final Set<String> MCP_PLATFORMS = new HashSet<String>(
            Arrays.asList("twitter", "weibo", "youtube", "bilibili"));
    private static final Set<String> APIFY_PLATFORMS = new HashSet<String>(
            Arrays.asList("facebook", "instagram", "tiktok", "telegram", "github"));
    private static final Set<String> SELF_STEPS = new HashSet<String>(Arrays.asList(
            "step4_text_compare",
            "step4_image_compare",
            "step5_validated",
            "step5_stream_text",
            "step5_stream_image",
            "step6_validated",
            "step8_img_analysis",
            "step9_context_views",
            "step10_context_pii",
            "step11_report",
            "step1_input_accounts"));

    private SourceTag() {
    }

    /** 平台通道：MCP→开源，Apify→付费。 */
    public static List<String> forPlatform(String platform) {
        if (platform == null) {
            return null;
        }
        String plat = platform.trim().toLowerCase();
        if (plat.isEmpty()) {
            return null;
        }
        if (MCP_PLATFORMS.contains(plat)) {
            return Collections.singletonList(OPEN);
        }
        if (APIFY_PLATFORMS.contains(plat)) {
            return Collections.singletonList(PAID);
        }
        return null;
    }

    /**
     * 按 step_key 解析标签；step1_seed 需传 seedPlatform。
     * streamsAsSelf：仅 03 的 step3_streams 为 true。
     */
    public static List<String> forStep(String stepKey, String seedPlatform, boolean streamsAsSelf) {
        if (stepKey == null) {
            return null;
        }
        String key = stepKey.trim();
        if (key.isEmpty()) {
            return null;
        }
        if ("step2_cross_platform".equals(key) || "step2_maigret".equals(key)
                || "step3_web_search".equals(key)) {
            return Collections.singletonList(OPEN);
        }
        if ("step6_osint_es".equals(key)) {
            return Arrays.asList(SELF, OFFLINE);
        }
        // [COLLISION_DEMO_FAKE] 关联碰撞假节点 — 正式版删除本段
        if ("step6_geo_verify".equals(key) || "step6_relation_graph".equals(key)) {
            return Collections.singletonList(OPEN);
        }
        if ("step6_rumor_sx".equals(key)) {
            return Arrays.asList(OFFLINE, SELF, FEATURE);
        }
        // [COLLISION_DEMO_FAKE] end
        if ("step3_streams".equals(key)) {
            return streamsAsSelf ? Collections.singletonList(SELF) : null;
        }
        if (SELF_STEPS.contains(key)) {
            return Collections.singletonList(SELF);
        }
        if ("step1_seed".equals(key)) {
            return forPlatform(seedPlatform);
        }
        if (key.startsWith("step6_video_") || key.startsWith("step7_video_")) {
            return Collections.singletonList(SELF);
        }
        String plat = platformFromPrefixedKey(key);
        if (plat != null) {
            return forPlatform(plat);
        }
        return null;
    }

    public static List<String> forStep(String stepKey, String seedPlatform) {
        return forStep(stepKey, seedPlatform, false);
    }

    public static List<String> forStep(String stepKey) {
        return forStep(stepKey, null, false);
    }

    /** INSERT 用 JSON 文本；无标签返回 null。 */
    public static String jsonForPlatform(String platform) {
        return toJson(forPlatform(platform));
    }

    public static String jsonForStep(String stepKey, String seedPlatform, boolean streamsAsSelf) {
        return toJson(forStep(stepKey, seedPlatform, streamsAsSelf));
    }

    public static String jsonForStep(String stepKey, String seedPlatform) {
        return jsonForStep(stepKey, seedPlatform, false);
    }

    public static String jsonForStep(String stepKey) {
        return jsonForStep(stepKey, null, false);
    }

    public static String toJson(List<String> tags) {
        if (tags == null || tags.isEmpty()) {
            return null;
        }
        StringBuilder sb = new StringBuilder(32);
        sb.append('[');
        for (int i = 0; i < tags.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append('"').append(escapeJson(tags.get(i))).append('"');
        }
        sb.append(']');
        return sb.toString();
    }

    /**
     * 树接口：库内 JSON 或旧单字符串 → List；空/非法 → null。
     */
    public static List<String> parseStored(Object raw) {
        if (raw == null) {
            return null;
        }
        String s = String.valueOf(raw).trim();
        if (s.isEmpty() || "null".equalsIgnoreCase(s)) {
            return null;
        }
        if (s.charAt(0) == '[') {
            List<String> out = new ArrayList<String>();
            String inner = s.substring(1, s.endsWith("]") ? s.length() - 1 : s.length()).trim();
            if (inner.isEmpty()) {
                return out;
            }
            int i = 0;
            while (i < inner.length()) {
                while (i < inner.length() && (inner.charAt(i) == ',' || Character.isWhitespace(inner.charAt(i)))) {
                    i++;
                }
                if (i >= inner.length()) {
                    break;
                }
                if (inner.charAt(i) != '"') {
                    // 容错：非标准片段，整段当单值
                    out.add(inner);
                    break;
                }
                i++;
                StringBuilder token = new StringBuilder();
                while (i < inner.length()) {
                    char c = inner.charAt(i);
                    if (c == '\\' && i + 1 < inner.length()) {
                        token.append(inner.charAt(i + 1));
                        i += 2;
                        continue;
                    }
                    if (c == '"') {
                        i++;
                        break;
                    }
                    token.append(c);
                    i++;
                }
                out.add(token.toString());
            }
            return out.isEmpty() ? null : out;
        }
        return Collections.singletonList(s);
    }

    private static String platformFromPrefixedKey(String key) {
        String[] prefixes = {
                "step4_profile_", "step3_profile_", "step6_post_", "step3_post_", "step7_post_"
        };
        for (String p : prefixes) {
            if (key.startsWith(p)) {
                return key.substring(p.length());
            }
        }
        return null;
    }

    private static String escapeJson(String s) {
        if (s == null) {
            return "";
        }
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
