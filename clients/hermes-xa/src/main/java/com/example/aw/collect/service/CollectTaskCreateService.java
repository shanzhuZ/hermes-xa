package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 采集任务预建：在调 Gateway 之前写入 hermes_tasks、用户一句话、步骤行。
 */
@Service
public class CollectTaskCreateService implements TaskCreateService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Override
    public String dbTaskType() {
        return "account_collect";
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public String createPendingTask(String taskId, String sessionId, String userMessage, String payloadJson) {
        if (taskId == null || taskId.trim().isEmpty()) {
            taskId = UUID.randomUUID().toString();
        }
        if (sessionId == null || sessionId.trim().isEmpty()) {
            throw new IllegalArgumentException("sessionId 不能为空");
        }
        if (userMessage == null || userMessage.trim().isEmpty()) {
            throw new IllegalArgumentException("message 不能为空");
        }
        if (!isCollectIntent(userMessage)) {
            throw new IllegalArgumentException("非采集意图消息，无法创建任务");
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

        int crossPlatform = parseCrossPlatform(userMessage);
        String seedJson = JSON.toJSONString(buildSeedJson(userMessage));

        collectTaskMapper.insertTask(taskId, sessionId, dbTaskType(), crossPlatform, seedJson);
        String clipped = userMessage.length() > 65535 ? userMessage.substring(0, 65535) : userMessage;
        collectTaskMapper.insertUserDialogue(taskId, sessionId, clipped, payloadJson);

        insertCrossPlatformSteps(taskId);
        if (crossPlatform == 0) {
            markSinglePlatformSkipped(taskId);
        }
        return taskId;
    }

    /**
     * 判断用户输入是否属于 01 账号采集 Skill 意图（与 Python Hook 规则对齐）。
     */
    private boolean isCollectIntent(String message) {
        Pattern p = Pattern.compile(
                "(account-intelligence-collect|账号信息采集|采集.*?(推特|twitter|微博|weibo|youtube|bilibili|"
                        + "instagram|tiktok|telegram|facebook|github|@))",
                Pattern.CASE_INSENSITIVE);
        return p.matcher(message).find();
    }

    /**
     * 从用户话里解析是否跨平台：默认跨平台，含「不跨平台/单平台」则为 0。
     */
    private int parseCrossPlatform(String message) {
        Pattern noCross = Pattern.compile("(仅当前平台|只采当前平台|只要当前平台|不跨平台|不要跨平台|不需要跨平台|单平台)");
        if (noCross.matcher(message).find()) {
            return 0;
        }
        return 1;
    }

    /**
     * 构造 seed_json 字段，供后续阶段读取平台与账号线索。
     */
    private Map<String, Object> buildSeedJson(String message) {
        Map<String, Object> seed = new HashMap<String, Object>();
        seed.put("platform", "twitter");
        Pattern platformHint = Pattern.compile(
                "(推特|twitter|微博|weibo|youtube|bilibili|instagram|ins|tiktok|telegram|tg|facebook|fb|脸书|github)",
                Pattern.CASE_INSENSITIVE);
        Matcher pm = platformHint.matcher(message);
        if (pm.find()) {
            String token = pm.group(1).toLowerCase();
            if ("微博".equals(token) || "weibo".equals(token)) {
                seed.put("platform", "weibo");
            } else if ("youtube".equals(token)) {
                seed.put("platform", "youtube");
            } else if ("bilibili".equals(token)) {
                seed.put("platform", "bilibili");
            } else if ("instagram".equals(token) || "ins".equals(token)) {
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
        Matcher hm = Pattern.compile("@([A-Za-z0-9_\\.]+)").matcher(message);
        if (hm.find()) {
            seed.put("account_handle", hm.group(1));
            seed.put("account_hint", hm.group(1));
        } else {
            String hint = message.trim();
            if (hint.length() > 128) {
                hint = hint.substring(0, 128);
            }
            seed.put("account_hint", hint);
        }
        return seed;
    }

    /**
     * 跨平台任务：插入完整步骤树（与 collect_01/phases.py ROOT_STEPS 一致）。
     */
    private void insertCrossPlatformSteps(String taskId) {
        collectTaskMapper.insertPhaseStep(taskId, "step1_seed", null, 10, "1", "步骤一：种子账号资料采集");
        collectTaskMapper.insertPhaseStep(taskId, "step2_cross_platform", null, 20, "2", "步骤二：跨平台账号收集");
        collectTaskMapper.insertPhaseStep(taskId, "step3_profiles", null, 30, "3", "步骤三：候选主页采集");
        collectTaskMapper.insertPhaseStep(taskId, "step3_streams", null, 40, "4", "步骤四：文本流与图片流拆分");
        collectTaskMapper.insertPhaseStep(taskId, "step4_text_compare", null, 41, "4.1", "步骤四：文本流对比");
        collectTaskMapper.insertPhaseStep(taskId, "step4_image_compare", null, 42, "4.2", "步骤四：图片流对比");
        collectTaskMapper.insertPhaseStep(taskId, "step5_validated", null, 50, "5", "步骤五：可信账号收敛");
        collectTaskMapper.insertPhaseStep(taskId, "step6_posts", null, 60, "6", "步骤六：分平台发文采集");
    }

    /**
     * 单平台任务：把用不到的步骤标记为 skipped（与 Python create_pending_task 一致）。
     */
    private void markSinglePlatformSkipped(String taskId) {
        collectTaskMapper.updateStepSkipped(taskId, "step2_cross_platform", "用户未要求跨平台采集");
        collectTaskMapper.updateStepSkipped(taskId, "step3_profiles", "单平台任务，跳过候选主页采集");
        collectTaskMapper.updateStepSkipped(taskId, "step3_streams", "单平台任务，跳过跨平台流拆分");
        collectTaskMapper.updateStepSkipped(taskId, "step4_text_compare", "单平台任务，跳过跨平台文本流比对");
        collectTaskMapper.updateStepSkipped(taskId, "step4_image_compare", "单平台任务，跳过跨平台图片流比对");
        collectTaskMapper.updateStepSkipped(taskId, "step5_validated", "单平台任务，默认种子账号直接进入发文采集");
    }
}
