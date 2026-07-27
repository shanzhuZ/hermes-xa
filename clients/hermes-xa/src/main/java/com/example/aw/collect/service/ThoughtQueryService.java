package com.example.aw.collect.service;

import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.stream.ThoughtFinalStore;
import com.example.aw.collect.stream.ThoughtTimelineStore;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 查询任务思考终稿与可回放时间线（与业务 final-answer 分离）。
 * <p>
 * 进行中：SSE 实时拼 assistant.delta；历史/刷新用 timeline（_thinking 整句）。
 */
@Service
public class ThoughtQueryService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Autowired
    private ThoughtFinalStore thoughtFinalStore;

    @Autowired
    private ThoughtTimelineStore thoughtTimelineStore;

    public Map<String, Object> getThoughts(String taskId) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "task_not_found");
            err.put("taskId", taskId);
            return err;
        }

        String status = task.get("status") == null ? "" : String.valueOf(task.get("status"));
        String finalContent = thoughtFinalStore.findFinalContent(taskId);
        boolean hasFinal = finalContent != null && finalContent.length() > 0;
        int lastThinkingSeq = thoughtTimelineStore.maxSeq(taskId);

        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("taskId", taskId);
        out.put("taskStatus", status);
        out.put("finalContent", hasFinal ? finalContent : null);
        out.put("hasFinal", Boolean.valueOf(hasFinal));
        out.put("mode", hasFinal ? "final" : "live");
        out.put("streamAvailable", Boolean.TRUE);
        out.put("lastThinkingSeq", Integer.valueOf(lastThinkingSeq));
        out.put("thoughtsStreamUrl", "/api/tasks/" + taskId + "/thoughts/stream");
        out.put("thoughtsTimelineUrl", "/api/tasks/" + taskId + "/thoughts/timeline");
        return out;
    }

    /**
     * 可回放时间线：仅 tool.progress + _thinking 整句。
     *
     * @param afterSeq 只返回 seq &gt; afterSeq 的事件；默认 0
     */
    public Map<String, Object> getTimeline(String taskId, long afterSeq) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "task_not_found");
            err.put("taskId", taskId);
            return err;
        }
        String status = task.get("status") == null ? "" : String.valueOf(task.get("status"));
        long after = Math.max(0L, afterSeq);
        List<Map<String, Object>> events = thoughtTimelineStore.listAfterSeq(taskId, after);
        int lastSeq = thoughtTimelineStore.maxSeq(taskId);
        if (events != null) {
            for (Map<String, Object> ev : events) {
                Object seqObj = ev.get("seq");
                if (seqObj instanceof Number) {
                    int s = ((Number) seqObj).intValue();
                    if (s > lastSeq) {
                        lastSeq = s;
                    }
                }
            }
        }

        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("taskId", taskId);
        out.put("taskStatus", status);
        out.put("afterSeq", Long.valueOf(after));
        out.put("lastSeq", Integer.valueOf(lastSeq));
        out.put("events", events == null ? new ArrayList<Map<String, Object>>() : events);
        out.put("thoughtsStreamUrl",
                "/api/tasks/" + taskId + "/thoughts/stream?afterSeq=" + lastSeq);
        return out;
    }
}
