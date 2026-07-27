package com.example.aw.collect.stream;

import com.example.aw.collect.mapper.ThoughtEventMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 思考流精简事件落库/查询：仅 tool.progress + toolName=_thinking。
 * <p>
 * 旁路写入：异常只打日志，绝不影响 SSE / 任务执行。
 */
@Component
public class ThoughtTimelineStore {

    private static final Logger log = LoggerFactory.getLogger(ThoughtTimelineStore.class);

    /** content 截断上限（与表 VARCHAR(2048) 对齐） */
    public static final int CONTENT_MAX = 2000;

    /** 单次 timeline 拉取上限 */
    public static final int TIMELINE_LIMIT = 5000;

    private static final String EVENT_TOOL_PROGRESS = "tool.progress";
    private static final String TOOL_THINKING = "_thinking";

    @Autowired
    private ThoughtEventMapper thoughtEventMapper;

    /**
     * 若事件为 _thinking 整句进度则落库；其它事件忽略。
     */
    public void persistIfThinkingProgress(Map<String, Object> event) {
        if (event == null || event.isEmpty()) {
            return;
        }
        String eventType = str(event.get("eventType"));
        String toolName = str(event.get("toolName"));
        if (!EVENT_TOOL_PROGRESS.equals(eventType) || !TOOL_THINKING.equals(toolName)) {
            return;
        }
        String taskId = str(event.get("taskId"));
        String content = str(event.get("content"));
        if (taskId.isEmpty() || content.isEmpty()) {
            return;
        }
        int seq = toInt(event.get("seq"), -1);
        if (seq < 0) {
            return;
        }
        String clipped = content.length() > CONTENT_MAX ? content.substring(0, CONTENT_MAX) : content;
        try {
            thoughtEventMapper.insertIgnore(taskId, seq, eventType, toolName, clipped);
        } catch (Exception e) {
            log.warn("思考流 _thinking 落库失败 taskId={} seq={}: {}", taskId, seq, e.getMessage());
        }
    }

    /**
     * 拉取 seq &gt; afterSeq 的事件（升序）。
     */
    public List<Map<String, Object>> listAfterSeq(String taskId, long afterSeq) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return Collections.emptyList();
        }
        try {
            List<Map<String, Object>> rows = thoughtEventMapper.selectAfterSeq(
                    taskId.trim(), afterSeq, TIMELINE_LIMIT);
            if (rows == null || rows.isEmpty()) {
                return Collections.emptyList();
            }
            List<Map<String, Object>> out = new ArrayList<Map<String, Object>>(rows.size());
            for (Map<String, Object> row : rows) {
                out.add(toApiEvent(row));
            }
            return out;
        } catch (Exception e) {
            log.warn("思考流 timeline 查询失败 taskId={}: {}", taskId, e.getMessage());
            return Collections.emptyList();
        }
    }

    public int maxSeq(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return 0;
        }
        try {
            Integer max = thoughtEventMapper.selectMaxSeq(taskId.trim());
            return max == null ? 0 : max.intValue();
        } catch (Exception e) {
            log.warn("思考流 maxSeq 查询失败 taskId={}: {}", taskId, e.getMessage());
            return 0;
        }
    }

    private static Map<String, Object> toApiEvent(Map<String, Object> row) {
        Map<String, Object> item = new LinkedHashMap<String, Object>();
        item.put("taskId", row.get("task_id"));
        item.put("eventType", row.get("event_type"));
        item.put("content", row.get("content"));
        item.put("toolName", row.get("tool_name"));
        item.put("seq", row.get("seq"));
        item.put("createdAt", row.get("created_at"));
        return item;
    }

    private static String str(Object v) {
        return v == null ? "" : String.valueOf(v).trim();
    }

    private static int toInt(Object v, int def) {
        if (v == null) {
            return def;
        }
        if (v instanceof Number) {
            return ((Number) v).intValue();
        }
        try {
            return Integer.parseInt(String.valueOf(v).trim());
        } catch (Exception e) {
            return def;
        }
    }
}
