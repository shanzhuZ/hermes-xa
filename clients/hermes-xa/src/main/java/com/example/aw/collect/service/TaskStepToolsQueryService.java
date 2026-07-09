package com.example.aw.collect.service;

import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 按任务与步骤键查询 hermes_tool_outputs 中成功的工具调用摘要。
 */
@Service
public class TaskStepToolsQueryService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /**
     * 查询某步骤下 status=success 的工具调用列表。
     */
    public Map<String, Object> listSuccessTools(String taskId, String stepKey) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "task_not_found");
            err.put("taskId", taskId);
            return err;
        }

        List<Map<String, Object>> rows = collectTaskMapper.selectSuccessToolOutputsByTaskAndPhase(taskId, stepKey);
        List<Map<String, Object>> records = new ArrayList<Map<String, Object>>();
        if (rows != null) {
            for (Map<String, Object> row : rows) {
                Map<String, Object> item = new LinkedHashMap<String, Object>();
                item.put("taskId", row.get("task_id"));
                item.put("phase", row.get("phase"));
                item.put("mcpServer", resolveMcpServer(row.get("mcp_server")));
                item.put("toolName", row.get("tool_name"));
                records.add(item);
            }
        }

        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("taskId", taskId);
        out.put("stepKey", stepKey);
        out.put("recordCount", records.size());
        out.put("records", records);
        return out;
    }

    /**
     * mcp_server 为空时视为内置 vision 工具。
     */
    private String resolveMcpServer(Object raw) {
        if (raw == null) {
            return "vision";
        }
        String text = String.valueOf(raw).trim();
        if (text.isEmpty()) {
            return "vision";
        }
        return text;
    }
}
