package com.example.aw.collect.stream;

import com.example.aw.collect.mapper.CollectTaskMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * 落库 assistant.completed 思考终稿（msg_type=thoughts_final）。
 * <p>
 * 异常不得向上抛，以免打断 SSE 中继。
 */
@Component
public class ThoughtFinalStore {

    private static final Logger log = LoggerFactory.getLogger(ThoughtFinalStore.class);

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /**
     * 保存或覆盖终稿；空内容忽略。多次 completed 后写覆盖先写。
     */
    public void saveAssistantCompleted(String taskId, String content) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        if (content == null || content.trim().isEmpty()) {
            return;
        }
        try {
            Map<String, Object> existing = collectTaskMapper.selectThoughtsFinal(taskId);
            if (existing != null && !existing.isEmpty() && existing.get("id") != null) {
                collectTaskMapper.updateThoughtsFinal(taskId, content);
                log.debug("覆盖思考终稿 taskId={} len={}", taskId, content.length());
                return;
            }
            Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
            if (task == null || task.isEmpty()) {
                log.warn("思考终稿落库跳过：任务不存在 taskId={}", taskId);
                return;
            }
            Object sessionObj = task.get("session_id");
            String sessionId = sessionObj == null ? "" : String.valueOf(sessionObj);
            collectTaskMapper.insertThoughtsFinal(taskId, sessionId, content);
            log.info("已落库思考终稿 taskId={} len={}", taskId, content.length());
        } catch (Exception e) {
            log.warn("思考终稿落库失败 taskId={}: {}", taskId, e.getMessage());
        }
    }

    /**
     * @return 终稿正文，无则 null
     */
    public String findFinalContent(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return null;
        }
        try {
            Map<String, Object> row = collectTaskMapper.selectThoughtsFinal(taskId);
            if (row == null || row.isEmpty() || row.get("content") == null) {
                return null;
            }
            String text = String.valueOf(row.get("content"));
            return text.isEmpty() ? null : text;
        } catch (Exception e) {
            log.warn("查询思考终稿失败 taskId={}: {}", taskId, e.getMessage());
            return null;
        }
    }
}
