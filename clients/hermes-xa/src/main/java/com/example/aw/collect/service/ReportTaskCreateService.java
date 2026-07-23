package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 04 账号画像写报 — 预建任务：step_plan + 七大阶段壳 + 业务子步。
 * 图片资产管线不进树（发文收口后后台跑）。
 */
@Service
public class ReportTaskCreateService implements TaskCreateService {

    public static final String STEP_PLAN = "step_plan";
    public static final String PHASE_LOCK_TARGET = "phase_lock_target";
    public static final String PHASE_DISCOVERY = "phase_discovery";
    public static final String PHASE_ACCOUNT_COLLECT = "phase_account_collect";
    public static final String PHASE_COLLISION = "phase_collision";
    public static final String PHASE_CONTENT = "phase_content";
    public static final String PHASE_ANALYSIS = "phase_analysis";
    public static final String PHASE_REPORT = "phase_report";

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Override
    public String dbTaskType() {
        return "account_report";
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public String createPendingTask(String taskId, String sessionId, String userMessage, String payloadJson) {
        if (taskId == null || taskId.trim().isEmpty()) {
            throw new IllegalArgumentException("taskId 不能为空");
        }
        if (sessionId == null || sessionId.trim().isEmpty()) {
            throw new IllegalArgumentException("sessionId 不能为空");
        }
        if (userMessage == null || userMessage.trim().isEmpty()) {
            throw new IllegalArgumentException("message 不能为空");
        }
        if (!isReportIntent(userMessage)) {
            throw new IllegalArgumentException("非画像写报意图消息，无法创建任务");
        }

        Map<String, Object> exists = collectTaskMapper.selectTaskById(taskId);
        if (exists != null && !exists.isEmpty()) {
            return taskId;
        }

        Map<String, Object> active = collectTaskMapper.selectActiveTaskBySessionId(sessionId);
        if (active != null && !active.isEmpty()) {
            throw new IllegalStateException(
                    "会话 " + sessionId + " 已有进行中的任务 " + active.get("task_id") + "，请等待结束后再发起");
        }

        Map<String, Object> seed = buildSeedJson(userMessage);
        String seedJson = JSON.toJSONString(seed);
        collectTaskMapper.insertTask(taskId, sessionId, dbTaskType(), 1, seedJson);
        String clipped = userMessage.length() > 65535 ? userMessage.substring(0, 65535) : userMessage;
        collectTaskMapper.insertUserDialogue(taskId, sessionId, clipped, payloadJson);
        insertReportSteps(taskId, stringVal(seed.get("platform")));
        return taskId;
    }

    private boolean isReportIntent(String message) {
        Pattern p = Pattern.compile(
                "(account-intelligence-report|account-intelligence-profile|画像写报|写报.*?"
                        + "(推特|twitter|微博|weibo|youtube|bilibili|"
                        + "instagram|tiktok|telegram|facebook|github|@))",
                Pattern.CASE_INSENSITIVE);
        return p.matcher(message).find();
    }

    private Map<String, Object> buildSeedJson(String message) {
        Map<String, Object> seed = new HashMap<String, Object>();
        seed.put("platform", "twitter");
        // 先剥 skill 前缀；短别名必须词界，避免 intelligence 内嵌 ig 等假阳性
        String text = stripSkillPrefix(message);
        Pattern platformHint = Pattern.compile(
                "(推特|twitter|微博|weibo|youtube|bilibili|instagram|\\bins\\b|\\big\\b|tiktok|telegram|\\btg\\b|facebook|\\bfb\\b|脸书|github)",
                Pattern.CASE_INSENSITIVE);
        Matcher pm = platformHint.matcher(text);
        if (pm.find()) {
            String token = pm.group(1).toLowerCase();
            if ("微博".equals(token) || "weibo".equals(token)) {
                seed.put("platform", "weibo");
            } else if ("youtube".equals(token)) {
                seed.put("platform", "youtube");
            } else if ("bilibili".equals(token)) {
                seed.put("platform", "bilibili");
            } else if ("instagram".equals(token) || "ins".equals(token) || "ig".equals(token)) {
                seed.put("platform", "instagram");
            } else if ("tiktok".equals(token)) {
                seed.put("platform", "tiktok");
            } else if ("telegram".equals(token) || "tg".equals(token)) {
                seed.put("platform", "telegram");
            } else if ("facebook".equals(token) || "fb".equals(token) || "脸书".equals(token)) {
                seed.put("platform", "facebook");
            } else if ("github".equals(token)) {
                seed.put("platform", "github");
            }
        }
        Matcher hm = Pattern.compile("@([A-Za-z0-9_\\.]+)").matcher(text);
        if (hm.find()) {
            seed.put("account_handle", hm.group(1));
            seed.put("account_hint", hm.group(1));
        } else {
            String hint = text.trim();
            if (hint.length() > 128) {
                hint = hint.substring(0, 128);
            }
            seed.put("account_hint", hint);
        }
        seed.put("parse_status", "pending");
        return seed;
    }

    /** 去掉 Hermes skill 名前缀，再解析平台/账号。 */
    private static String stripSkillPrefix(String message) {
        if (message == null) {
            return "";
        }
        return message.trim().replaceFirst(
                "(?i)^(account-intelligence-collect|account-expansion|account-intelligence-expand|"
                        + "account-intelligence-verification|account-intelligence-verify|"
                        + "account-intelligence-report|account-intelligence-profile)\\b\\s*",
                "");
    }

    /**
     * 插树：step_plan + 七大阶段壳 + 业务子步，全部 pending。
     * 七大壳挂在 step_plan 下（流程图：根 → 制定执行计划 → 1..7）。
     * 规划收口与启动 Agent 由 {@link ReportPlanBootstrap} / CollectSubmitService 负责。
     */
    private void insertReportSteps(String taskId, String seedPlatform) {
        collectTaskMapper.insertPhaseStep(taskId, STEP_PLAN, null, 5, "0", "制定执行计划");

        collectTaskMapper.insertPhaseStep(taskId, PHASE_LOCK_TARGET, STEP_PLAN, 100, "1", "1. 锁定目标");
        collectTaskMapper.insertPhaseStep(taskId, PHASE_DISCOVERY, STEP_PLAN, 200, "2", "2. 线索发现");
        collectTaskMapper.insertPhaseStep(taskId, PHASE_ACCOUNT_COLLECT, STEP_PLAN, 300, "3", "3. 账号采集");
        collectTaskMapper.insertPhaseStep(taskId, PHASE_COLLISION, STEP_PLAN, 400, "4", "4. 关联碰撞");
        collectTaskMapper.insertPhaseStep(taskId, PHASE_CONTENT, STEP_PLAN, 500, "5", "5. 内容采集");
        collectTaskMapper.insertPhaseStep(taskId, PHASE_ANALYSIS, STEP_PLAN, 600, "6", "6. 深度研判");
        collectTaskMapper.insertPhaseStep(taskId, PHASE_REPORT, STEP_PLAN, 700, "7", "7. 报告生成");

        collectTaskMapper.insertPhaseStep(
                taskId, "step1_seed", PHASE_LOCK_TARGET, 110, "1.1", seedAgentTitle(seedPlatform));
        collectTaskMapper.insertPhaseStep(
                taskId, "step2_maigret", PHASE_DISCOVERY, 210, "2.1", "Maigret Agent 跨平台收集");
        collectTaskMapper.insertPhaseStep(
                taskId, "step3_web_search", PHASE_DISCOVERY, 220, "2.2", "网页检索 Agent 候选发现");
        collectTaskMapper.insertPhaseStep(
                taskId, "step4_profiles", PHASE_ACCOUNT_COLLECT, 310, "3.1", "MCP/Apify Agent 候选主页采集");
        collectTaskMapper.insertPhaseStep(
                taskId, "step5_streams", PHASE_COLLISION, 410, "4.1", "信息核验流 Agent 核查");
        collectTaskMapper.insertPhaseStep(
                taskId, "step6_validated", PHASE_COLLISION, 420, "4.2", "相似账号认定 Agent");
        collectTaskMapper.insertPhaseStep(
                taskId, "step7_posts", PHASE_CONTENT, 510, "5.1", "跨平台发文采集 Agent");
        collectTaskMapper.insertPhaseStep(
                taskId, "step8_img_analysis", PHASE_ANALYSIS, 610, "6.1", "图片流 Agent 分析");
        collectTaskMapper.insertPhaseStep(
                taskId, "step9_context_views", PHASE_ANALYSIS, 620, "6.2", "观点与涉华分析 Agent");
        collectTaskMapper.insertPhaseStep(
                taskId, "step10_context_pii", PHASE_ANALYSIS, 630, "6.3", "PII 与圈层分析 Agent");
        collectTaskMapper.insertPhaseStep(
                taskId, "step11_report", PHASE_REPORT, 710, "7.1", "画像报告 Agent");
    }

    private String seedAgentTitle(String platform) {
        String plat = platform == null || platform.trim().isEmpty()
                ? "twitter" : platform.trim().toLowerCase();
        String label = platformLabel(plat);
        if ("twitter".equals(plat) || "weibo".equals(plat)
                || "youtube".equals(plat) || "bilibili".equals(plat)) {
            return label + " MCP Agent 采集";
        }
        return "Apify Agent · " + label + " 采集";
    }

    private String platformLabel(String platform) {
        if ("twitter".equals(platform)) {
            return "Twitter";
        }
        if ("weibo".equals(platform)) {
            return "微博";
        }
        if ("youtube".equals(platform)) {
            return "YouTube";
        }
        if ("bilibili".equals(platform)) {
            return "B站";
        }
        if ("instagram".equals(platform)) {
            return "Instagram";
        }
        if ("tiktok".equals(platform)) {
            return "TikTok";
        }
        if ("telegram".equals(platform)) {
            return "Telegram";
        }
        if ("facebook".equals(platform)) {
            return "Facebook";
        }
        if ("github".equals(platform)) {
            return "GitHub";
        }
        return platform;
    }

    private static String stringVal(Object v) {
        return v == null ? "" : String.valueOf(v).trim();
    }
}
