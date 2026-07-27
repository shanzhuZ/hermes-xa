package com.example.aw.collect.service;

import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 查询对话答案态（历史详情 report / final-answer）。
 * <p>
 * 优先级与 /api/dialogues 折叠一致：{@code cancelled} &gt; {@code summary} &gt; {@code user_input}。
 */
@Service
public class TaskFinalAnswerQueryService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /**
     * 返回任务答案态全文：取消标记 / 业务终稿 / 仅有提问时的 user_input。
     */
    public Map<String, Object> getFinalAnswer(String taskId) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "task_not_found");
            err.put("taskId", taskId);
            return err;
        }

        Map<String, Object> preferred = collectTaskMapper.selectLatestAssistantReply(taskId);
        if (preferred != null && !preferred.isEmpty() && preferred.get("content") != null) {
            String msgType = preferred.get("msg_type") == null
                    ? ""
                    : String.valueOf(preferred.get("msg_type")).trim();
            Map<String, Object> out = new LinkedHashMap<String, Object>();
            out.put("taskId", taskId);
            out.put("type", mapType(msgType));
            out.put("msgType", msgType.isEmpty() ? null : msgType);
            out.put("content", preferred.get("content"));
            out.put("createdAt", preferred.get("created_at"));
            out.put("ready", Boolean.TRUE);
            if ("cancelled".equals(msgType)) {
                out.put("message", "用户已结束任务");
            } else if ("user_input".equals(msgType)) {
                // 尚无 summary/cancelled：仅有提问，ready 仍 true 以便历史列表有可展示内容
                out.put("message", "尚无终稿，当前为用户提问");
            }
            return out;
        }

        Map<String, Object> pending = new LinkedHashMap<String, Object>();
        pending.put("taskId", taskId);
        pending.put("type", "pending");
        pending.put("msgType", null);
        pending.put("content", null);
        pending.put("createdAt", null);
        pending.put("ready", Boolean.FALSE);
        pending.put("message", "模型终稿尚未入库，请继续轮询 tree 或稍后再试");
        return pending;
    }

    private static String mapType(String msgType) {
        if ("cancelled".equals(msgType)) {
            return "cancelled";
        }
        if ("summary".equals(msgType)) {
            return "summary";
        }
        if ("user_input".equals(msgType)) {
            return "user_input";
        }
        return msgType == null || msgType.isEmpty() ? "pending" : msgType;
    }
}
