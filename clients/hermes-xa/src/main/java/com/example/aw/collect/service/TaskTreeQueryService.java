package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
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

        Map<String, List<String>> toolNamesByStep = collectToolNamesByStep(tools);
        String taskType = stringVal(task.get("task_type"));
        List<Map<String, Object>> nodes = buildStepHierarchy(taskId, taskType, steps, toolNamesByStep);

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
        tree.put("taskType", task.get("task_type"));
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
     * 按 parent_step_key 组装层级：step6_post_* 挂在 step3_profiles.children 下；
     * 顶层 nodes 仅保留无父步骤的根节点。
     */
    private List<Map<String, Object>> buildStepHierarchy(
            String taskId,
            String taskType,
            List<Map<String, Object>> steps,
            Map<String, List<String>> toolNamesByStep) {
        List<Map<String, Object>> roots = new ArrayList<Map<String, Object>>();
        if (steps == null || steps.isEmpty()) {
            return roots;
        }
        Map<String, Map<String, Object>> nodeByKey = new LinkedHashMap<String, Map<String, Object>>();
        for (Map<String, Object> step : steps) {
            String stepKey = stringVal(step.get("step_key"));
            if (isHiddenLegacyStep(taskType, stepKey)) {
                continue;
            }
            nodeByKey.put(stepKey, buildStepNode(taskId, step, toolNamesByStep.get(stepKey)));
        }
        for (Map<String, Object> step : steps) {
            String stepKey = stringVal(step.get("step_key"));
            if (isHiddenLegacyStep(taskType, stepKey)) {
                continue;
            }
            Map<String, Object> node = nodeByKey.get(stepKey);
            if (node == null) {
                continue;
            }
            String parentKey = stringVal(step.get("parent_step_key"));
            if (parentKey.isEmpty()) {
                roots.add(node);
                continue;
            }
            Map<String, Object> parent = nodeByKey.get(parentKey);
            if (parent != null) {
                childrenOf(parent).add(node);
            } else {
                roots.add(node);
            }
        }
        return roots;
    }

    private Map<String, Object> buildStepNode(
            String taskId,
            Map<String, Object> step,
            List<String> toolNames) {
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
        if (toolNames != null && !toolNames.isEmpty()) {
            node.put("toolNames", toolNames);
        }
        node.put("children", new ArrayList<Map<String, Object>>());
        return node;
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> childrenOf(Map<String, Object> node) {
        Object raw = node.get("children");
        if (raw instanceof List) {
            return (List<Map<String, Object>>) raw;
        }
        List<Map<String, Object>> children = new ArrayList<Map<String, Object>>();
        node.put("children", children);
        return children;
    }

    /**
     * 02 扩建遗留的 step6_posts 不再展示；01 采集 step6_posts 为第六大点父节点，须保留。
     */
    private boolean isHiddenLegacyStep(String taskType, String stepKey) {
        return "account_expand".equals(taskType) && "step6_posts".equals(stepKey);
    }

    /** 按步骤键收集 MCP 工具名（挂在步骤节点 toolNames 字段，不再嵌套 tool 子节点） */
    private Map<String, List<String>> collectToolNamesByStep(List<Map<String, Object>> tools) {
        Map<String, List<String>> buckets = new HashMap<String, List<String>>();
        if (tools == null) {
            return buckets;
        }
        for (Map<String, Object> row : tools) {
            String name = stringVal(row.get("tool_name"));
            if (isSkipTool(name)) {
                continue;
            }
            String stepKey = resolveToolStepKey(row, tools);
            if (!buckets.containsKey(stepKey)) {
                buckets.put(stepKey, new ArrayList<String>());
            }
            buckets.get(stepKey).add(name);
        }
        return buckets;
    }

    /**
     * 不参与进度展示的工具（与 Python task_tree 一致）。
     */
    private boolean isSkipTool(String toolName) {
        if (toolName == null || toolName.isEmpty()) {
            return true;
        }
        return "skill_view".equals(toolName)
                || "clarify".equals(toolName)
                || "tool_search".equals(toolName)
                || "describe_tool".equals(toolName)
                || "todo".equals(toolName)
                || "terminal".equals(toolName)
                || toolName.startsWith("browser_");
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
        if ("mcp_weibo_get_user_feeds".equals(toolName) || "mcp_weibo_get_feeds".equals(toolName)) {
            return "step6_post_weibo";
        }
        if ("mcp_apify_get_actor_run".equals(toolName)) {
            return "step3_profiles";
        }
        return "step6_posts";
    }

    /**
     * 优先 phase=step6_post_*；平台采集工具按工具名映射到 step6_post_{platform}；
     * get_dataset_items 在 phase 仍为 step3_profiles 时向前找最近一次 Apify Actor。
     */
    private String resolveToolStepKey(Map<String, Object> row, List<Map<String, Object>> allTools) {
        String phase = stringVal(row.get("phase"));
        String toolName = stringVal(row.get("tool_name"));
        if (phase.startsWith("step6_post_")) {
            return phase;
        }
        String postStep = remapCollectPostStepKey(toolName);
        if (postStep != null) {
            return postStep;
        }
        if ("mcp_apify_get_dataset_items".equals(toolName)) {
            String inferred = inferDatasetPostStep(row, allTools);
            if (inferred != null) {
                return inferred;
            }
        }
        if (phase.startsWith("step")) {
            return phase;
        }
        return resolveToolStepKey(toolName);
    }

    /** 向前查找与本条 dataset 对应的 Apify Actor，映射到 step6_post_{platform} */
    private String inferDatasetPostStep(Map<String, Object> row, List<Map<String, Object>> allTools) {
        if (allTools == null || allTools.isEmpty()) {
            return null;
        }
        long toolId = toLong(row.get("id"));
        if (toolId <= 0) {
            return null;
        }
        String lastActorStep = null;
        for (Map<String, Object> t : allTools) {
            long id = toLong(t.get("id"));
            if (id >= toolId) {
                break;
            }
            String name = stringVal(t.get("tool_name"));
            if (!name.startsWith("mcp_apify_")
                    || "mcp_apify_get_dataset_items".equals(name)
                    || "mcp_apify_get_actor_run".equals(name)) {
                continue;
            }
            String mapped = remapCollectPostStepKey(name);
            if (mapped != null) {
                lastActorStep = mapped;
            }
        }
        return lastActorStep;
    }

    private long toLong(Object value) {
        if (value instanceof Number) {
            return ((Number) value).longValue();
        }
        try {
            return Long.parseLong(stringVal(value));
        } catch (NumberFormatException e) {
            return -1L;
        }
    }

    /** 发文/平台采集类工具 → step6_post_{platform}（兼容历史 phase 挂在 step3_profiles） */
    private String remapCollectPostStepKey(String toolName) {
        if ("mcp_twitter_get_user_tweets".equals(toolName)) {
            return "step6_post_twitter";
        }
        if ("mcp_youtube_analyze_channel_videos".equals(toolName)) {
            return "step6_post_youtube";
        }
        if ("mcp_youtube_get_channel_stats".equals(toolName)) {
            return "step6_post_youtube";
        }
        if ("mcp_weibo_get_profile".equals(toolName)
                || "mcp_weibo_get_user_feeds".equals(toolName)
                || "mcp_weibo_get_feeds".equals(toolName)) {
            return "step6_post_weibo";
        }
        if ("mcp_apify_apify__instagram_scraper".equals(toolName)) {
            return "step6_post_instagram";
        }
        if ("mcp_apify_clockworks__tiktok_scraper".equals(toolName)) {
            return "step6_post_tiktok";
        }
        if ("mcp_apify_vujeen__telegram_channel_scraper".equals(toolName)) {
            return "step6_post_telegram";
        }
        if ("mcp_apify_headlessagent__facebook_profile_post_scraper".equals(toolName)) {
            return "step6_post_facebook";
        }
        if ("mcp_apify_knotless_cadence__github_profile_scraper".equals(toolName)) {
            return "step6_post_github";
        }
        return null;
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
