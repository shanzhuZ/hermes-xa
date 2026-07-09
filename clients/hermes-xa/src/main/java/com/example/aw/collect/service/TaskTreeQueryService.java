package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 将 MySQL 中的步骤、工具输出聚合成密塔式简易进度树 JSON。
 */
@Service
public class TaskTreeQueryService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /**
     * 构建整棵进度树，供前端轮询渲染。
     */
    public Map<String, Object> buildTaskTree(String taskId) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "task_not_found");
            err.put("taskId", taskId);
            return err;
        }

        Map<String, Object> userRow = collectTaskMapper.selectUserInput(taskId);
        Map<String, Object> summaryRow = collectTaskMapper.selectSummary(taskId);
        List<Map<String, Object>> steps = collectTaskMapper.selectPhaseSteps(taskId);
        List<Map<String, Object>> tools = collectTaskMapper.selectToolOutputs(taskId);

        Map<String, List<Map<String, Object>>> toolBuckets = bucketToolsByStep(taskId, tools);
        List<Map<String, Object>> nodes = new ArrayList<Map<String, Object>>();
        if (steps != null) {
            for (Map<String, Object> step : steps) {
                String stepKey = stringVal(step.get("step_key"));
                String status = stringVal(step.get("status"));
                if (status.isEmpty()) {
                    status = "pending";
                }
                Map<String, Object> node = new LinkedHashMap<String, Object>();
                node.put("id", stepKey);
                node.put("type", "step");
                node.put("stepKey", stepKey);
                node.put("stepNode", step.get("step_node"));
                node.put("parentStepKey", step.get("parent_step_key"));
                node.put("title", step.get("title"));
                node.put("status", status);
                node.put("statusLabel", statusLabel(status));
                node.put("message", step.get("message"));
                node.put("progressPct", step.get("progress_pct"));
                node.put("startedAt", stringVal(step.get("started_at")));
                node.put("finishedAt", stringVal(step.get("finished_at")));
                node.put("detailRef", "/api/tasks/" + taskId + "/nodes/" + stepKey);
                List<Map<String, Object>> children = toolBuckets.get(stepKey);
                if (children == null) {
                    children = new ArrayList<Map<String, Object>>();
                }
                node.put("children", children);
                nodes.add(node);
            }
        }

        String rootTitle = userRow != null ? stringVal(userRow.get("content")) : "";
        if (rootTitle.length() > 200) {
            rootTitle = rootTitle.substring(0, 200);
        }
        if (rootTitle.isEmpty()) {
            rootTitle = "用户提问";
        }

        Map<String, Object> root = new LinkedHashMap<String, Object>();
        root.put("id", "user_query");
        root.put("type", "query");
        root.put("title", rootTitle);
        root.put("status", "completed");
        root.put("statusLabel", "结论明确");
        root.put("detailRef", "/api/tasks/" + taskId + "/nodes/user_query");

        Map<String, Object> summary = null;
        if (summaryRow != null && !summaryRow.isEmpty()) {
            String content = stringVal(summaryRow.get("content"));
            summary = new LinkedHashMap<String, Object>();
            summary.put("id", "summary");
            summary.put("type", "summary");
            summary.put("title", "采集报告");
            summary.put("status", "completed");
            summary.put("statusLabel", "结论明确");
            if (content.length() > 300) {
                summary.put("preview", content.substring(0, 300) + "…");
            } else {
                summary.put("preview", content);
            }
            summary.put("detailRef", "/api/tasks/" + taskId + "/nodes/summary");
        }

        Map<String, Object> tree = new LinkedHashMap<String, Object>();
        tree.put("taskId", taskId);
        tree.put("sessionId", task.get("session_id"));
        tree.put("status", task.get("status"));
        tree.put("currentPhase", task.get("current_phase"));
        tree.put("crossPlatform", task.get("cross_platform"));
        tree.put("root", root);
        tree.put("nodes", nodes);
        tree.put("summary", summary);
        return tree;
    }

    /**
     * 查询单个节点详情（用户问题 / 步骤 / 工具 / 终稿）。
     */
    public Map<String, Object> getNodeDetail(String taskId, String nodeId) {
        if ("user_query".equals(nodeId)) {
            Map<String, Object> row = collectTaskMapper.selectUserInput(taskId);
            Map<String, Object> out = new LinkedHashMap<String, Object>();
            out.put("nodeId", nodeId);
            out.put("type", "query");
            out.put("content", row != null ? row.get("content") : null);
            out.put("createdAt", row != null ? stringVal(row.get("created_at")) : "");
            return out;
        }
        if ("summary".equals(nodeId)) {
            Map<String, Object> row = collectTaskMapper.selectSummary(taskId);
            Map<String, Object> out = new LinkedHashMap<String, Object>();
            out.put("nodeId", nodeId);
            out.put("type", "summary");
            out.put("content", row != null ? row.get("content") : null);
            out.put("createdAt", row != null ? stringVal(row.get("created_at")) : "");
            return out;
        }
        if (nodeId.startsWith("tool_")) {
            long toolId = Long.parseLong(nodeId.substring(5));
            Map<String, Object> row = collectTaskMapper.selectToolOutputById(taskId, toolId);
            if (row == null || row.isEmpty()) {
                Map<String, Object> err = new LinkedHashMap<String, Object>();
                err.put("error", "node_not_found");
                err.put("nodeId", nodeId);
                return err;
            }
            Map<String, Object> out = new LinkedHashMap<String, Object>();
            out.put("nodeId", nodeId);
            out.put("type", "tool");
            out.put("toolName", row.get("tool_name"));
            out.put("status", row.get("status"));
            out.put("durationMs", row.get("duration_ms"));
            out.put("executedAt", stringVal(row.get("executed_at")));
            out.put("toolArgs", parseJsonField(row.get("tool_args")));
            out.put("toolOutput", parseJsonField(row.get("tool_output")));
            return out;
        }

        Map<String, Object> row = collectTaskMapper.selectPhaseStep(taskId, nodeId);
        if (row == null || row.isEmpty()) {
            Map<String, Object> err = new LinkedHashMap<String, Object>();
            err.put("error", "node_not_found");
            err.put("nodeId", nodeId);
            return err;
        }
        String status = stringVal(row.get("status"));
        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("nodeId", nodeId);
        out.put("type", "step");
        out.put("stepKey", row.get("step_key"));
        out.put("title", row.get("title"));
        out.put("status", status);
        out.put("statusLabel", statusLabel(status));
        out.put("message", row.get("message"));
        out.put("payload", parseJsonField(row.get("payload_json")));
        out.put("startedAt", stringVal(row.get("started_at")));
        out.put("finishedAt", stringVal(row.get("finished_at")));
        return out;
    }

    /**
     * 把工具调用按步骤键分组，作为步骤节点的 children。
     */
    private Map<String, List<Map<String, Object>>> bucketToolsByStep(String taskId, List<Map<String, Object>> tools) {
        Map<String, List<Map<String, Object>>> buckets = new HashMap<String, List<Map<String, Object>>>();
        if (tools == null) {
            return buckets;
        }
        for (Map<String, Object> row : tools) {
            String name = stringVal(row.get("tool_name"));
            if (isSkipTool(name)) {
                continue;
            }
            String stepKey = resolveToolStepKey(row);
            Map<String, Object> child = new LinkedHashMap<String, Object>();
            Object idObj = row.get("id");
            String toolNodeId = "tool_" + String.valueOf(idObj);
            child.put("id", toolNodeId);
            child.put("type", "tool");
            child.put("title", name);
            String toolStatus = stringVal(row.get("status"));
            child.put("status", toolStatus.isEmpty() ? "success" : toolStatus);
            if ("success".equals(toolStatus) || toolStatus.isEmpty()) {
                child.put("statusLabel", "结论明确");
            } else {
                child.put("statusLabel", "信息缺失");
            }
            child.put("durationMs", row.get("duration_ms"));
            child.put("executedAt", stringVal(row.get("executed_at")));
            child.put("detailRef", "/api/tasks/" + taskId + "/nodes/" + toolNodeId);
            if (!buckets.containsKey(stepKey)) {
                buckets.put(stepKey, new ArrayList<Map<String, Object>>());
            }
            buckets.get(stepKey).add(child);
        }
        return buckets;
    }

    /**
     * 不参与进度展示的工具（与 Python task_tree 一致）。
     */
    private boolean isSkipTool(String toolName) {
        return "skill_view".equals(toolName)
                || "clarify".equals(toolName)
                || "tool_search".equals(toolName)
                || "describe_tool".equals(toolName)
                || "todo".equals(toolName)
                || "terminal".equals(toolName);
    }

    /**
     * 根据工具名判断应挂在哪个步骤节点下（与 phases.py TOOL_PRIMARY_STEP 对齐）。
     */
    private String resolveToolStepKey(String toolName) {
        if ("mcp_twitter_get_user_info".equals(toolName)) {
            return "step1_seed";
        }
        if ("mcp_maigret_collect_accounts".equals(toolName)) {
            return "step2_cross_platform";
        }
        if ("mcp_youtube_get_channel_stats".equals(toolName)
                || "mcp_weibo_get_profile".equals(toolName)
                || "mcp_apify_apify__instagram_scraper".equals(toolName)
                || "mcp_apify_clockworks__tiktok_scraper".equals(toolName)
                || "mcp_apify_vujeen__telegram_channel_scraper".equals(toolName)
                || "mcp_apify_headlessagent__facebook_profile_post_scraper".equals(toolName)
                || "mcp_apify_knotless_cadence__github_profile_scraper".equals(toolName)
                || "mcp_apify_get_dataset_items".equals(toolName)) {
            return "step3_profiles";
        }
        if ("mcp_ocr_perform_ocr".equals(toolName) || "mcp_vision_analyze".equals(toolName)
                || "vision_analyze".equals(toolName)) {
            return "step4_image_compare";
        }
        if ("mcp_twitter_get_user_tweets".equals(toolName)) {
            return "step6_post_twitter";
        }
        if ("mcp_youtube_analyze_channel_videos".equals(toolName)) {
            return "step6_post_youtube";
        }
        if ("mcp_weibo_get_user_feeds".equals(toolName)) {
            return "step6_post_weibo";
        }
        return "step6_posts";
    }

    /**
     * 优先使用 hermes_tool_outputs.phase（已与 collect_phase_steps.step_key 对齐），
     * 历史数据若仍为 resolve_seed 等大阶段名则按工具名推断。
     */
    private String resolveToolStepKey(Map<String, Object> row) {
        String phase = stringVal(row.get("phase"));
        if (phase.startsWith("step")) {
            return phase;
        }
        return resolveToolStepKey(stringVal(row.get("tool_name")));
    }

    private String statusLabel(String status) {
        if ("completed".equals(status)) {
            return "结论明确";
        }
        if ("running".equals(status)) {
            return "进行中";
        }
        if ("failed".equals(status)) {
            return "信息缺失";
        }
        if ("skipped".equals(status)) {
            return "已跳过";
        }
        return "待执行";
    }

    private Object parseJsonField(Object raw) {
        if (raw == null) {
            return null;
        }
        if (!(raw instanceof String)) {
            return raw;
        }
        String text = (String) raw;
        if (text.trim().isEmpty()) {
            return null;
        }
        try {
            return JSON.parse(text);
        } catch (Exception e) {
            return text;
        }
    }

    private String stringVal(Object o) {
        return o == null ? "" : String.valueOf(o);
    }
}
