package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.registry.TaskTypeRegistry;
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

        String seedJson = JSON.toJSONString(buildSeedJson(userMessage));
        collectTaskMapper.insertTask(taskId, sessionId, dbTaskType(), 1, seedJson);
        String clipped = userMessage.length() > 65535 ? userMessage.substring(0, 65535) : userMessage;
        collectTaskMapper.insertUserDialogue(taskId, sessionId, clipped, payloadJson);
        insertExpandSteps(taskId);
        return taskId;
    }

    private boolean isExpandIntent(String message) {
        Pattern p = Pattern.compile(
                "(account-expansion|account-intelligence-expand|账号扩建|扩建.*?(推特|twitter|微博|weibo|@))",
                Pattern.CASE_INSENSITIVE);
        return p.matcher(message).find();
    }

    private Map<String, Object> buildSeedJson(String message) {
        Map<String, Object> seed = new HashMap<String, Object>();
        seed.put("platform", "twitter");
        Pattern platformHint = Pattern.compile("(推特|twitter|微博|weibo|youtube|bilibili)", Pattern.CASE_INSENSITIVE);
        Matcher pm = platformHint.matcher(message);
        if (pm.find()) {
            String token = pm.group(1).toLowerCase();
            if ("微博".equals(token) || "weibo".equals(token)) {
                seed.put("platform", "weibo");
            } else if ("youtube".equals(token)) {
                seed.put("platform", "youtube");
            } else if ("bilibili".equals(token)) {
                seed.put("platform", "bilibili");
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
     * 扩建四段式步骤树（共用 step_key，标题按 02 UI）。
     */
    private void insertExpandSteps(String taskId) {
        collectTaskMapper.insertPhaseStep(taskId, "step1_seed", null, 10, "1", "一、账号扩建收集：种子资料");
        collectTaskMapper.insertPhaseStep(taskId, "step2_cross_platform", null, 20, "1.2", "一、账号扩建收集：跨平台发现");
        collectTaskMapper.insertPhaseStep(taskId, "step3_profiles", null, 30, "2", "二、多平台信息采集：主页与发文");
        collectTaskMapper.insertPhaseStep(taskId, "step3_streams", null, 40, "3", "三、账号核查：流拆分");
        collectTaskMapper.insertPhaseStep(taskId, "step4_text_compare", null, 41, "3.1", "三、账号核查：文本流对比");
        collectTaskMapper.insertPhaseStep(taskId, "step4_image_compare", null, 42, "3.2", "三、账号核查：图片流对比");
        collectTaskMapper.insertPhaseStep(taskId, "step5_validated", null, 50, "4", "四、可信账号输出");
        collectTaskMapper.insertPhaseStep(taskId, "step6_posts", null, 60, "2.9", "二、多平台信息采集：发文汇总");
    }
}
