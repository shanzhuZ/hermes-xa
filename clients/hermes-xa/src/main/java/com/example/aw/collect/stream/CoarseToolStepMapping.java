package com.example.aw.collect.stream;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 工具名 → 粗同步目标步骤（对齐 collect_01 / expand_02 / verify_03 / report_04）。
 * <p>
 * Apify / 采集入库类 / Vision：仅允许粗 running，禁止粗 completed。
 * <p>
 * Vision 多轮按图片流收口，completed 只能由 Hook 在 is_image_compare_ready 后写入。
 * 覆盖：account_collect / account_expand / account_verify / account_report。
 */
public final class CoarseToolStepMapping {

    private CoarseToolStepMapping() {
    }

    private static final Set<String> IGNORE_TOOLS;
    /** 04 写报：网页检索工具驱动 step3_web_search（其它业务仍忽略） */
    private static final Set<String> REPORT_WEB_TOOLS;
    private static final Map<String, String> TOOL_PLATFORM;
    private static final Set<String> POST_TOOLS;
    private static final Set<String> COARSE_COMPLETE_OK;
    private static final Set<String> APIFY_ACTOR_OR_DATASET;
    private static final Set<String> VISION_OCR_TOOLS;

    static {
        Set<String> ignore = new HashSet<String>();
        ignore.add("_thinking");
        ignore.add("skill_view");
        ignore.add("clarify");
        ignore.add("tool_search");
        ignore.add("describe_tool");
        ignore.add("todo");
        ignore.add("terminal");
        ignore.add("web_search");
        ignore.add("web_extract");
        ignore.add("browser_navigate");
        ignore.add("browser_vision");
        ignore.add("browser_click");
        ignore.add("browser_type");
        ignore.add("mcp_firecrawl_firecrawl_search");
        ignore.add("mcp_firecrawl_firecrawl_scrape");
        IGNORE_TOOLS = Collections.unmodifiableSet(ignore);

        Set<String> reportWeb = new HashSet<String>();
        reportWeb.add("web_search");
        reportWeb.add("web_extract");
        reportWeb.add("browser_navigate");
        reportWeb.add("browser_vision");
        reportWeb.add("browser_click");
        reportWeb.add("browser_type");
        REPORT_WEB_TOOLS = Collections.unmodifiableSet(reportWeb);

        Map<String, String> plat = new HashMap<String, String>();
        plat.put("mcp_twitter_get_user_info", "twitter");
        plat.put("mcp_twitter_get_user_tweets", "twitter");
        plat.put("mcp_youtube_get_channel_stats", "youtube");
        plat.put("mcp_youtube_analyze_channel_videos", "youtube");
        plat.put("mcp_weibo_get_profile", "weibo");
        plat.put("mcp_weibo_get_user_feeds", "weibo");
        plat.put("mcp_weibo_get_feeds", "weibo");
        plat.put("mcp_bilibili_get_user_info", "bilibili");
        plat.put("mcp_apify_apify__instagram_scraper", "instagram");
        plat.put("mcp_apify_clockworks__tiktok_scraper", "tiktok");
        plat.put("mcp_apify_vujeen__telegram_channel_scraper", "telegram");
        plat.put("mcp_apify_headlessagent__facebook_profile_post_scraper", "facebook");
        plat.put("mcp_apify_knotless_cadence__github_profile_scraper", "github");
        TOOL_PLATFORM = Collections.unmodifiableMap(plat);

        Set<String> posts = new HashSet<String>();
        posts.add("mcp_twitter_get_user_tweets");
        posts.add("mcp_youtube_analyze_channel_videos");
        posts.add("mcp_weibo_get_user_feeds");
        posts.add("mcp_weibo_get_feeds");
        posts.add("mcp_apify_get_dataset_items");
        POST_TOOLS = Collections.unmodifiableSet(posts);

        Set<String> okComplete = new HashSet<String>();
        // Vision/OCR 禁止粗 completed（多轮收口交给 Hook）
        COARSE_COMPLETE_OK = Collections.unmodifiableSet(okComplete);

        Set<String> apify = new HashSet<String>();
        apify.add("mcp_apify_get_dataset_items");
        apify.add("mcp_apify_get_actor_run");
        apify.add("mcp_apify_apify__instagram_scraper");
        apify.add("mcp_apify_clockworks__tiktok_scraper");
        apify.add("mcp_apify_vujeen__telegram_channel_scraper");
        apify.add("mcp_apify_headlessagent__facebook_profile_post_scraper");
        apify.add("mcp_apify_knotless_cadence__github_profile_scraper");
        APIFY_ACTOR_OR_DATASET = Collections.unmodifiableSet(apify);

        Set<String> vision = new HashSet<String>();
        vision.add("mcp_ocr_perform_ocr");
        vision.add("mcp_ocr_perform_batch_ocr");
        vision.add("mcp_vision_analyze");
        vision.add("vision_analyze");
        VISION_OCR_TOOLS = Collections.unmodifiableSet(vision);
    }

    public static final class Target {
        public final String stepKey;
        public final boolean allowCoarseComplete;

        public Target(String stepKey, boolean allowCoarseComplete) {
            this.stepKey = stepKey;
            this.allowCoarseComplete = allowCoarseComplete;
        }
    }

    /**
     * @param taskType 库内 task_type，如 account_verify
     * @return 不可映射则 null
     */
    public static Target resolve(String taskType, String toolName) {
        if (toolName == null || toolName.trim().isEmpty()) {
            return null;
        }
        String tool = toolName.trim();
        // 04 写报：web/browser 驱动 step3，不走全局忽略
        if (IGNORE_TOOLS.contains(tool)) {
            if (!("account_report".equals(taskType) && REPORT_WEB_TOOLS.contains(tool))) {
                return null;
            }
        }
        if ("account_verify".equals(taskType)) {
            return resolveVerify(tool);
        }
        if ("account_collect".equals(taskType)) {
            return resolveCollect(tool);
        }
        if ("account_expand".equals(taskType)) {
            return resolveExpand(tool);
        }
        if ("account_report".equals(taskType)) {
            return resolveReport(tool);
        }
        return null;
    }

    /**
     * 粗同步点亮目标步骤前，须先（或同时）running 的逻辑父节点。
     * 父不得晚于子：如 step4_* → step3_streams；step6_post_* → step3_profiles/step6_posts。
     */
    public static List<String> parentsToEnsureRunning(String taskType, String stepKey) {
        List<String> parents = new ArrayList<String>();
        if (stepKey == null || stepKey.isEmpty()) {
            return parents;
        }
        if ("step4_text_compare".equals(stepKey) || "step4_image_compare".equals(stepKey)) {
            parents.add("step3_streams");
            return parents;
        }
        if (stepKey.startsWith("step6_post_")) {
            if ("account_collect".equals(taskType)) {
                parents.add("step6_posts");
            } else {
                // expand / verify 等：发文子步骤挂在 step3_profiles 下
                parents.add("step3_profiles");
            }
            return parents;
        }
        if (stepKey.startsWith("step3_post_") || stepKey.startsWith("step3_profile_")) {
            parents.add("step3_profiles");
        }
        return parents;
    }

    private static Target resolveVerify(String tool) {
        if (VISION_OCR_TOOLS.contains(tool)) {
            return new Target("step4_image_compare", false);
        }
        if (COARSE_COMPLETE_OK.contains(tool)) {
            return new Target("step4_image_compare", true);
        }
        if ("mcp_maigret_collect_accounts".equals(tool)) {
            return new Target("step2_cross_platform", false);
        }
        if ("mcp_apify_get_dataset_items".equals(tool) || "mcp_apify_get_actor_run".equals(tool)) {
            return new Target("step3_profiles", false);
        }
        String platform = TOOL_PLATFORM.get(tool);
        if (platform != null) {
            if (POST_TOOLS.contains(tool) && !"mcp_apify_get_dataset_items".equals(tool)) {
                return new Target("step3_post_" + platform, false);
            }
            return new Target("step3_profile_" + platform, false);
        }
        return null;
    }

    /** 01 采集 */
    private static Target resolveCollect(String tool) {
        if (VISION_OCR_TOOLS.contains(tool)) {
            return new Target("step4_image_compare", false);
        }
        if (COARSE_COMPLETE_OK.contains(tool)) {
            return new Target("step4_image_compare", true);
        }
        if ("mcp_twitter_get_user_info".equals(tool)) {
            return new Target("step1_seed", false);
        }
        if ("mcp_maigret_collect_accounts".equals(tool)) {
            return new Target("step2_cross_platform", false);
        }
        if ("mcp_twitter_get_user_tweets".equals(tool)) {
            return new Target("step6_post_twitter", false);
        }
        if ("mcp_youtube_analyze_channel_videos".equals(tool)) {
            return new Target("step6_post_youtube", false);
        }
        if ("mcp_weibo_get_user_feeds".equals(tool) || "mcp_weibo_get_feeds".equals(tool)) {
            return new Target("step6_post_weibo", false);
        }
        if (APIFY_ACTOR_OR_DATASET.contains(tool)
                || "mcp_youtube_get_channel_stats".equals(tool)
                || "mcp_weibo_get_profile".equals(tool)
                || "mcp_bilibili_get_user_info".equals(tool)) {
            return new Target("step3_profiles", false);
        }
        return null;
    }

    /** 02 扩建：主页 MCP/部分 Apify 挂在 step6_post_* 子节点 */
    private static Target resolveExpand(String tool) {
        if (VISION_OCR_TOOLS.contains(tool)) {
            return new Target("step4_image_compare", false);
        }
        if ("mcp_twitter_get_user_info".equals(tool)) {
            return new Target("step1_seed", false);
        }
        if ("mcp_maigret_collect_accounts".equals(tool)) {
            return new Target("step2_cross_platform", false);
        }
        if ("mcp_twitter_get_user_tweets".equals(tool)) {
            return new Target("step6_post_twitter", false);
        }
        if ("mcp_youtube_analyze_channel_videos".equals(tool)
                || "mcp_youtube_get_channel_stats".equals(tool)) {
            return new Target("step6_post_youtube", false);
        }
        if ("mcp_weibo_get_user_feeds".equals(tool)
                || "mcp_weibo_get_feeds".equals(tool)
                || "mcp_weibo_get_profile".equals(tool)) {
            return new Target("step6_post_weibo", false);
        }
        if ("mcp_bilibili_get_user_info".equals(tool)) {
            return new Target("step6_post_bilibili", false);
        }
        if (APIFY_ACTOR_OR_DATASET.contains(tool)) {
            String platform = TOOL_PLATFORM.get(tool);
            if (platform != null
                    && !"mcp_apify_get_dataset_items".equals(tool)
                    && !"mcp_apify_get_actor_run".equals(tool)) {
                return new Target("step6_post_" + platform, false);
            }
            return new Target("step3_profiles", false);
        }
        return null;
    }

    /** 04 画像写报（step 编号与 01 不同） */
    private static Target resolveReport(String tool) {
        if (REPORT_WEB_TOOLS.contains(tool)) {
            return new Target("step3_web_search", false);
        }
        if (VISION_OCR_TOOLS.contains(tool)) {
            return new Target("step5_streams", false);
        }
        if ("mcp_twitter_get_user_info".equals(tool)
                || "mcp_youtube_get_channel_stats".equals(tool)
                || "mcp_weibo_get_profile".equals(tool)
                || "mcp_bilibili_get_user_info".equals(tool)) {
            return new Target("step1_seed", false);
        }
        if ("mcp_maigret_collect_accounts".equals(tool)) {
            return new Target("step2_maigret", false);
        }
        if ("mcp_twitter_get_user_tweets".equals(tool)) {
            return new Target("step7_post_twitter", false);
        }
        if ("mcp_youtube_analyze_channel_videos".equals(tool)) {
            return new Target("step7_post_youtube", false);
        }
        if ("mcp_weibo_get_user_feeds".equals(tool) || "mcp_weibo_get_feeds".equals(tool)) {
            return new Target("step7_post_weibo", false);
        }
        String platform = TOOL_PLATFORM.get(tool);
        if ("mcp_apify_get_dataset_items".equals(tool) || "mcp_apify_get_actor_run".equals(tool)) {
            return new Target("step4_profiles", false);
        }
        if (APIFY_ACTOR_OR_DATASET.contains(tool) && platform != null) {
            return new Target("step4_profile_" + platform, false);
        }
        if (platform != null && !POST_TOOLS.contains(tool)) {
            return new Target("step4_profile_" + platform, false);
        }
        return null;
    }
}
