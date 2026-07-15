package com.example.aw.collect.service;

import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.stream.ThoughtFinalStore;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 查询任务思考终稿（与业务 final-answer 分离）。
 * <p>
 * 进行中 I1：不返回 partial；有 thoughts_final 则 mode=final。
 */
@Service
public class ThoughtQueryService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Autowired
    private ThoughtFinalStore thoughtFinalStore;

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

        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("taskId", taskId);
        out.put("taskStatus", status);
        out.put("finalContent", hasFinal ? finalContent : null);
        out.put("hasFinal", Boolean.valueOf(hasFinal));
        out.put("mode", hasFinal ? "final" : "live");
        out.put("streamAvailable", Boolean.TRUE);
        out.put("thoughtsStreamUrl", "/api/tasks/" + taskId + "/thoughts/stream");
        return out;
    }
}
