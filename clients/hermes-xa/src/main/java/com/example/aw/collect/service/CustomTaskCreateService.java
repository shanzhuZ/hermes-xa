package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.HashMap;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 05 自定义任务预建：自然语言开任务，仅预插「制定计划」根步骤；
 * 后续流程图由模型通过 flow upsert/begin/finish 动态写入。
 */
@Service
public class CustomTaskCreateService implements TaskCreateService {

    private static final Pattern SKILL_NAME = Pattern.compile(
            "account-intelligence-custom", Pattern.CASE_INSENSITIVE);

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Override
    public String dbTaskType() {
        return "account_custom";
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
        if (!isCustomIntent(userMessage)) {
            throw new IllegalArgumentException("非自定义意图消息，无法创建任务");
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

        Map<String, Object> seed = new HashMap<String, Object>();
        seed.put("mode", "custom");
        String text = stripSkillPrefix(userMessage);
        if (text.length() > 512) {
            text = text.substring(0, 512);
        }
        seed.put("user_hint", text);

        collectTaskMapper.insertTask(taskId, sessionId, dbTaskType(), 0, JSON.toJSONString(seed));
        String clipped = userMessage.length() > 65535 ? userMessage.substring(0, 65535) : userMessage;
        collectTaskMapper.insertUserDialogue(taskId, sessionId, clipped, payloadJson);

        // 启动即可见：制定计划；真正业务节点由 flow.upsert 追加
        collectTaskMapper.insertPhaseStep(taskId, "step_plan", null, 10, "1", "制定执行计划");
        collectTaskMapper.updateStepStatus(taskId, "step_plan", "running", "分析用户问题并生成步骤计划");
        return taskId;
    }

    /**
     * 自定义：Gateway 消息带 skill 名前缀即可（前端 taskType=custom 时由 Registry 自动加）。
     */
    private boolean isCustomIntent(String message) {
        return SKILL_NAME.matcher(message).find();
    }

    private static String stripSkillPrefix(String message) {
        if (message == null) {
            return "";
        }
        return message.trim().replaceFirst(
                "(?i)^account-intelligence-custom\\b\\s*",
                "");
    }
}
