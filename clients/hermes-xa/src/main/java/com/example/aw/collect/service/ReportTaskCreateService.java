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
 * 04 账号画像写报 — 预建任务与 11 步步骤树。
 */
@Service
public class ReportTaskCreateService implements TaskCreateService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Override
    public String dbTaskType() {
        return "account_report";
    }

    @Override
    @Transactional(rollbackFor = Exception.class)
    public String createPendingTask(String taskId, String sessionId, String userMessage) {
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

        String seedJson = JSON.toJSONString(buildSeedJson(userMessage));
        collectTaskMapper.insertTask(taskId, sessionId, dbTaskType(), 1, seedJson);
        String clipped = userMessage.length() > 65535 ? userMessage.substring(0, 65535) : userMessage;
        collectTaskMapper.insertUserDialogue(taskId, sessionId, clipped);
        insertReportSteps(taskId);
        return taskId;
    }

    private boolean isReportIntent(String message) {
        Pattern p = Pattern.compile(
                "(account-intelligence-report|account-intelligence-profile|画像写报|写报.*?"
                        + "(推特|twitter|微博|weibo|youtube|instagram|telegram|@))",
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
        seed.put("parse_status", "pending");
        return seed;
    }

    /**
     * 写报 11 步根节点；step1 初始 running，其余 pending。
     */
    private void insertReportSteps(String taskId) {
        collectTaskMapper.insertPhaseStep(taskId, "step1_seed", null, 10, "1", "步骤一：种子 profile");
        collectTaskMapper.insertPhaseStep(taskId, "step2_maigret", null, 20, "2", "步骤二：Maigret 跨平台发现");
        collectTaskMapper.insertPhaseStep(taskId, "step3_web_search", null, 30, "3", "步骤三：网页检索候选");
        collectTaskMapper.insertPhaseStep(taskId, "step4_profiles", null, 40, "4", "步骤四：候选主页采集");
        collectTaskMapper.insertPhaseStep(taskId, "step5_streams", null, 50, "5", "步骤五：文本/图片流核查");
        collectTaskMapper.insertPhaseStep(taskId, "step6_validated", null, 60, "6", "步骤六：相似账号认定");
        collectTaskMapper.insertPhaseStep(taskId, "step7_posts", null, 70, "7", "步骤七：发文采集");
        collectTaskMapper.insertPhaseStep(taskId, "step8_img_analysis", null, 80, "8", "步骤八：图片流分析");
        collectTaskMapper.insertPhaseStep(taskId, "step9_context_views", null, 90, "9", "步骤九：观点与涉华分析");
        collectTaskMapper.insertPhaseStep(taskId, "step10_context_pii", null, 100, "10", "步骤十：PII 与圈层分析");
        collectTaskMapper.insertPhaseStep(taskId, "step11_report", null, 110, "11", "步骤十一：画像报告");
        collectTaskMapper.updateStepStatus(
                taskId,
                "step1_seed",
                "running",
                "等待种子 profile 采集…");
    }
}
