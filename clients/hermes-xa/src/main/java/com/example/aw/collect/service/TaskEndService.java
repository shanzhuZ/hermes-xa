package com.example.aw.collect.service;

import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 用户「结束」任务：hermes_tasks → cancelled，并在 hermes_user_dialogues
 * 将非 summary 的 assistant 对话 msg_type 标为 cancelled（无则插入一条）。
 * <p>
 * 不改步骤树；不改 user_input / summary。
 * Agent 停靠 Gateway drainStream 发现 cancelled 后断 SSE（与 failed 同路径）。
 * 不杀视频等后台子进程。
 */
@Service
public class TaskEndService {

    private static final String END_MESSAGE = "用户结束";
    private static final String DIALOGUE_CANCELLED_MSG_TYPE = "cancelled";

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /**
     * 结束任务：pending/running → cancelled，并标记对话 msg_type=cancelled。
     *
     * @param taskId 任务 ID
     * @return ok、taskId、status、statusLabel
     */
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> endTask(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "taskId_required");
        }
        String tid = taskId.trim();
        Map<String, Object> task = collectTaskMapper.selectTaskById(tid);
        if (task == null || task.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "task_not_found");
        }

        Object stObj = task.get("status");
        String status = stObj == null ? "" : String.valueOf(stObj).trim();
        if (!"pending".equals(status) && !"running".equals(status)) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "task_status_not_endable");
        }

        int n = collectTaskMapper.markTaskCancelled(tid, END_MESSAGE);
        if (n <= 0) {
            // 并发下状态已变
            throw new ResponseStatusException(HttpStatus.CONFLICT, "task_status_not_endable");
        }

        markDialogueCancelled(tid, task);

        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("ok", Boolean.TRUE);
        body.put("taskId", tid);
        body.put("status", "cancelled");
        body.put("statusLabel", "已取消");
        body.put("dialogueMsgType", DIALOGUE_CANCELLED_MSG_TYPE);
        return body;
    }

    /** 非 summary 的 assistant → msg_type=cancelled；没有则插入标记行。 */
    private void markDialogueCancelled(String taskId, Map<String, Object> task) {
        int updated = collectTaskMapper.markLatestAssistantDialogueCancelled(taskId);
        if (updated > 0) {
            return;
        }
        Object sidObj = task.get("session_id");
        String sessionId = sidObj == null ? "" : String.valueOf(sidObj).trim();
        collectTaskMapper.insertCancelledDialogue(taskId, sessionId, END_MESSAGE);
    }
}
