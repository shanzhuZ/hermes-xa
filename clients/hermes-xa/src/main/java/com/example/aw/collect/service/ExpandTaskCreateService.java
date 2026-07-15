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
 * 02 账号扩建 — 预建任务与步骤树（默认跨平台）。
 */
@Service
public class ExpandTaskCreateService implements TaskCreateService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Override
    public String dbTaskType() {
        return "account_expand";
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
        if (!isExpandIntent(userMessage)) {
            throw new IllegalArgumentException("非账号扩建意图消息，无法创建任务");
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
        insertExpandSteps(taskId, stringVal(seed.get("platform")));
        return taskId;
    }

    private boolean isExpandIntent(String message) {
        Pattern p = Pattern.compile(
                "(account-expansion|account-intelligence-expand|账号扩建|"
                        + "扩建.*?(推特|twitter|微博|weibo|youtube|bilibili|"
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
     * 扩建步骤树（共用 step_key，Agent 风格标题，与 expand_02/phases.py 一致）。
     */
    private void insertExpandSteps(String taskId, String seedPlatform) {
        collectTaskMapper.insertPhaseStep(taskId, "step1_seed", null, 10, "1", seedAgentTitle(seedPlatform));
        collectTaskMapper.insertPhaseStep(taskId, "step2_cross_platform", null, 20, "1.2", "Maigret Agent 跨平台收集");
        collectTaskMapper.insertPhaseStep(taskId, "step3_profiles", null, 30, "2", "MCP/Apify Agent 主页与发文采集");
        collectTaskMapper.insertPhaseStep(taskId, "step3_streams", null, 40, "3", "信息核验流 Agent 拆分");
        collectTaskMapper.insertPhaseStep(taskId, "step4_text_compare", null, 41, "3.1", "文本流 Agent 对比");
        collectTaskMapper.insertPhaseStep(taskId, "step4_image_compare", null, 42, "3.2", "图片流 Agent 分析");
        collectTaskMapper.insertPhaseStep(taskId, "step5_validated", null, 50, "4", "可信账号核验 Agent");
        // 02 不再插入 step6_posts(2.9)：发文子步骤直接挂在 step3_profiles 下
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
