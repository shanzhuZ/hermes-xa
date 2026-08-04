package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.SourceTag;
import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashMap;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 03 账号核查 — 预建任务与步骤树（多种子互比，无 Maigret/step6）。
 */
@Service
public class VerifyTaskCreateService implements TaskCreateService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Override
    public String dbTaskType() {
        return "account_verify";
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
        if (!isVerifyIntent(userMessage)) {
            throw new IllegalArgumentException("非账号核查意图消息，无法创建任务");
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

        String seedJson = JSON.toJSONString(buildSeedJson());
        collectTaskMapper.insertTask(taskId, sessionId, dbTaskType(), 1, seedJson);
        String clipped = userMessage.length() > 65535 ? userMessage.substring(0, 65535) : userMessage;
        collectTaskMapper.insertUserDialogue(taskId, sessionId, clipped, payloadJson);
        insertVerifySteps(taskId);
        return taskId;
    }

    private boolean isVerifyIntent(String message) {
        Pattern p = Pattern.compile(
                "(account-intelligence-verification|account-intelligence-verify|账号核查|核查.*?"
                        + "(推特|twitter|微博|weibo|facebook|fb|脸书|youtube|instagram|ins|"
                        + "telegram|tg|tiktok|抖音|github|bilibili|b站))",
                Pattern.CASE_INSENSITIVE);
        return p.matcher(message).find();
    }

    private Map<String, Object> buildSeedJson() {
        Map<String, Object> seed = new HashMap<String, Object>();
        seed.put("input_accounts", new Object[0]);
        seed.put("parse_status", "pending");
        return seed;
    }

    /**
     * 03 核查步骤树：step1_input_accounts 初始 running，其余 pending（Agent 风格标题）。
     */
    private void insertVerifySteps(String taskId) {
        collectTaskMapper.insertPhaseStep(taskId, "step1_input_accounts", null, 10, "1",
                "种子账号确认 Agent", SourceTag.jsonForStep("step1_input_accounts"));
        collectTaskMapper.insertPhaseStep(taskId, "step3_profiles", null, 30, "2",
                "MCP/Apify Agent 主页与发文采集", SourceTag.jsonForStep("step3_profiles"));
        collectTaskMapper.insertPhaseStep(taskId, "step3_streams", null, 40, "3",
                "发文风格与领域归纳 Agent", SourceTag.jsonForStep("step3_streams", null, true));
        // 4.1 / 4.2 挂在 step3_streams 下（与粗同步 parentsToEnsureRunning 一致）
        collectTaskMapper.insertPhaseStep(taskId, "step4_text_compare", "step3_streams", 41, "4.1",
                "文本流 Agent 对比", SourceTag.jsonForStep("step4_text_compare"));
        collectTaskMapper.insertPhaseStep(taskId, "step4_image_compare", "step3_streams", 42, "4.2",
                "图片流 Agent 分析", SourceTag.jsonForStep("step4_image_compare"));
        collectTaskMapper.insertPhaseStep(taskId, "step5_validated", null, 50, "5",
                "账号核验 Agent", SourceTag.jsonForStep("step5_validated"));
        collectTaskMapper.updateStepStatus(
                taskId,
                "step1_input_accounts",
                "running",
                "等待 Agent 确认种子账号…");
    }
}
