package com.example.aw.collect.service;

import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 查询模型最终回答（优先三节报告 summary，否则取最新 assistant 回复）。
 */
@Service
public class TaskFinalAnswerQueryService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /**
     * 返回任务最终模型输出全文，供前端单独展示报告区。
     */
    public Map<String, Object> getFinalAnswer(String taskId) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "task_not_found");
            err.put("taskId", taskId);
            return err;
        }

        Map<String, Object> summary = collectTaskMapper.selectSummary(taskId);
        if (summary != null && !summary.isEmpty() && summary.get("content") != null) {
            Map<String, Object> out = new LinkedHashMap<String, Object>();
            out.put("taskId", taskId);
            out.put("type", "summary");
            out.put("msgType", "summary");
            out.put("content", summary.get("content"));
            out.put("createdAt", summary.get("created_at"));
            out.put("ready", true);
            return out;
        }

        Map<String, Object> assistant = collectTaskMapper.selectLatestAssistantReply(taskId);
        if (assistant != null && !assistant.isEmpty() && assistant.get("content") != null) {
            Map<String, Object> out = new LinkedHashMap<String, Object>();
            out.put("taskId", taskId);
            out.put("type", "assistant");
            out.put("msgType", assistant.get("msg_type"));
            out.put("content", assistant.get("content"));
            out.put("createdAt", assistant.get("created_at"));
            out.put("ready", true);
            return out;
        }

        Map<String, Object> pending = new LinkedHashMap<String, Object>();
        pending.put("taskId", taskId);
        pending.put("type", "pending");
        pending.put("msgType", null);
        pending.put("content", null);
        pending.put("createdAt", null);
        pending.put("ready", false);
        pending.put("message", "模型终稿尚未入库，请继续轮询 tree 或稍后再试");
        return pending;
    }
}
