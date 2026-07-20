package com.example.aw.collect.stream;

import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.service.FlowStepService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * 落库 assistant.completed 思考终稿（msg_type=thoughts_final）。
 * <p>
 * 历史对话接口不展示 thoughts_final（与 summary 重复）；仅 /thoughts 查询使用。
 * 若已有 summary 终稿，则不再重复写入 thoughts_final。
 * 自定义任务（account_custom）在终稿落库后会兜底收口步骤树与任务状态。
 * 异常不得向上抛，以免打断 SSE 中继。
 */
@Component
public class ThoughtFinalStore {

    private static final Logger log = LoggerFactory.getLogger(ThoughtFinalStore.class);

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Autowired
    private FlowStepService flowStepService;

    /**
     * 保存或覆盖终稿；空内容忽略。已有 summary 时跳过（避免与业务终稿重复落库）。
     */
    public void saveAssistantCompleted(String taskId, String content) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        if (content == null || content.trim().isEmpty()) {
            return;
        }
        try {
            Map<String, Object> summary = collectTaskMapper.selectSummary(taskId);
            if (summary != null && !summary.isEmpty() && summary.get("content") != null) {
                String summaryText = String.valueOf(summary.get("content")).trim();
                if (!summaryText.isEmpty()) {
                    log.debug("已有 summary，跳过 thoughts_final 落库 taskId={}", taskId);
                    // 即便跳过 thoughts_final，自定义任务仍可能需要收口步骤树
                    reconcileCustomIfNeeded(taskId);
                    return;
                }
            }
            Map<String, Object> existing = collectTaskMapper.selectThoughtsFinal(taskId);
            if (existing != null && !existing.isEmpty() && existing.get("id") != null) {
                collectTaskMapper.updateThoughtsFinal(taskId, content);
                log.debug("覆盖思考终稿 taskId={} len={}", taskId, content.length());
                reconcileCustomIfNeeded(taskId);
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
            reconcileCustomIfNeeded(taskId, task);
        } catch (Exception e) {
            log.warn("思考终稿落库失败 taskId={}: {}", taskId, e.getMessage());
        }
    }

    private void reconcileCustomIfNeeded(String taskId) {
        try {
            Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
            reconcileCustomIfNeeded(taskId, task);
        } catch (Exception e) {
            log.warn("自定义任务终稿收口失败 taskId={}: {}", taskId, e.getMessage());
        }
    }

    private void reconcileCustomIfNeeded(String taskId, Map<String, Object> task) {
        if (task == null || task.isEmpty()) {
            return;
        }
        Object typeObj = task.get("task_type");
        String taskType = typeObj == null ? "" : String.valueOf(typeObj).trim();
        if (!"account_custom".equals(taskType)) {
            return;
        }
        try {
            flowStepService.reconcileOnFinalAnswer(taskId);
            log.info("自定义任务终稿已收口步骤树 taskId={}", taskId);
        } catch (Exception e) {
            log.warn("自定义任务终稿收口失败 taskId={}: {}", taskId, e.getMessage());
        }
    }

    /**
     * @return 终稿正文：优先 thoughts_final，否则回退 summary（供 /thoughts 使用）
     */
    public String findFinalContent(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return null;
        }
        try {
            Map<String, Object> row = collectTaskMapper.selectThoughtsFinal(taskId);
            if (row != null && !row.isEmpty() && row.get("content") != null) {
                String text = String.valueOf(row.get("content")).trim();
                if (!text.isEmpty()) {
                    return text;
                }
            }
            Map<String, Object> summary = collectTaskMapper.selectSummary(taskId);
            if (summary != null && !summary.isEmpty() && summary.get("content") != null) {
                String text = String.valueOf(summary.get("content")).trim();
                return text.isEmpty() ? null : text;
            }
            return null;
        } catch (Exception e) {
            log.warn("查询思考终稿失败 taskId={}: {}", taskId, e.getMessage());
            return null;
        }
    }
}
