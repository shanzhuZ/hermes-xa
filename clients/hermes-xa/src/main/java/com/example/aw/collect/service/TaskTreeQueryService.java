package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 将 MySQL 中的步骤、工具输出聚合成密塔式简易进度树 JSON。
 */
@Service
public class TaskTreeQueryService {

    /** 04 写报七大阶段壳：进度条只在这些节点上按后代叶子终态占比现算。 */
    private static final Set<String> REPORT_PHASE_SHELLS = new HashSet<String>(Arrays.asList(
            "phase_lock_target",
            "phase_discovery",
            "phase_account_collect",
            "phase_collision",
            "phase_content",
            "phase_analysis",
            "phase_report"
    ));

    private static final Set<String> TERMINAL_STATUSES = new HashSet<String>(Arrays.asList(
            "completed", "skipped", "failed"
    ));

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Autowired
    private ReportPhaseNarrativeConfig reportPhaseNarrativeConfig;

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
        // 04：七大壳阶段锚点进度（stages/currentStage/progressPct）；值变化时回写 progress_pct
        if ("account_report".equals(taskType)) {
            applyReportPhaseShellProgress(taskId, nodes);
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
     * 按 parent_step_key 组装层级：
     * - 01：step4_text/image_compare → step3_streams.children；step6_post_* → step6_posts.children
     * - 02/03：step4_* → step3_streams；发文子步骤 → step3_profiles
     * - 04：step4_profile_* → step4_profiles；step7_post_* → step7_posts
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

    /**
     * 04 七大壳进度：阶段锚点（stages + currentStage → progressPct）。
     * 仅壳节点 showProgress=true；子步不展示进度条。
     * progressPct = round(100 * currentStage / (stages.length - 1))；前端可对 pct 做过渡动画。
     * 旁白仅挂 step_plan + 七大父壳（见 report-phase-narratives.json），不碰其它节点。
     */
    private void applyReportPhaseShellProgress(String taskId, List<Map<String, Object>> nodes) {
        if (nodes == null || nodes.isEmpty()) {
            return;
        }
        for (Map<String, Object> node : nodes) {
            applyReportPhaseShellProgress(taskId, childrenOf(node));
            String stepKey = stringVal(node.get("stepKey"));
            if (REPORT_PHASE_SHELLS.contains(stepKey)) {
                node.put("showProgress", Boolean.TRUE);
                StageProgress sp = resolveReportShellStageProgress(node);
                node.put("stages", sp.stages);
                node.put("currentStage", Integer.valueOf(sp.currentStage));
                node.put("progressPct", Integer.valueOf(sp.progressPct));
                attachPhaseNarrative(node, stepKey);
                try {
                    collectTaskMapper.updateStepProgressPctIfChanged(taskId, stepKey, sp.progressPct);
                } catch (Exception ignored) {
                    // 回写失败不影响树接口；进度仍以响应 progressPct 为准
                }
            } else if ("step_plan".equals(stepKey)) {
                // 规划层：只挂旁白，不走七壳进度条
                attachPhaseNarrative(node, stepKey);
            }
        }
    }

    /** 规划层/父壳旁白：固定两字段，前端按 status 选用。 */
    private void attachPhaseNarrative(Map<String, Object> node, String stepKey) {
        if (reportPhaseNarrativeConfig == null || stepKey == null || stepKey.isEmpty()) {
            return;
        }
        Map<String, String> pair = reportPhaseNarrativeConfig.get(stepKey);
        if (pair == null || pair.isEmpty()) {
            return;
        }
        node.put("running_content", pair.get("running_content"));
        node.put("completed_content", pair.get("completed_content"));
    }

    /** 七壳阶段锚点结果。 */
    private static final class StageProgress {
        private final List<String> stages;
        private final int currentStage;
        private final int progressPct;

        private StageProgress(List<String> stages, int currentStage) {
            this.stages = stages;
            int last = stages.size() - 1;
            int idx = currentStage;
            if (idx < 0) {
                idx = 0;
            }
            if (idx > last) {
                idx = last;
            }
            this.currentStage = idx;
            this.progressPct = last <= 0 ? 100 : (int) Math.round(100.0 * idx / last);
        }
    }

    private StageProgress resolveReportShellStageProgress(Map<String, Object> shellNode) {
        String shellKey = stringVal(shellNode.get("stepKey"));
        String shellStatus = stringVal(shellNode.get("status"));
        if ("phase_lock_target".equals(shellKey)) {
            return stageLockTarget(shellNode, shellStatus);
        }
        if ("phase_discovery".equals(shellKey)) {
            return stageDiscovery(shellNode, shellStatus);
        }
        if ("phase_account_collect".equals(shellKey)) {
            return stageAccountCollect(shellNode, shellStatus);
        }
        if ("phase_collision".equals(shellKey)) {
            return stageCollision(shellNode, shellStatus);
        }
        if ("phase_content".equals(shellKey)) {
            return stageContent(shellNode, shellStatus);
        }
        if ("phase_analysis".equals(shellKey)) {
            return stageAnalysis(shellNode, shellStatus);
        }
        if ("phase_report".equals(shellKey)) {
            return stageReport(shellNode, shellStatus);
        }
        List<String> fallback = Arrays.asList("开始", "完成");
        return new StageProgress(fallback, isTerminalStatus(shellStatus) ? 1 : 0);
    }

    /** 1.锁定目标：开始 → 进行中 → 完成 */
    private StageProgress stageLockTarget(Map<String, Object> shell, String shellStatus) {
        List<String> stages = Arrays.asList("开始", "进行中", "完成");
        if (isTerminalStatus(shellStatus)) {
            return new StageProgress(stages, 2);
        }
        String s1 = findDescendantStatus(shell, "step1_seed");
        if (isActiveStatus(s1) || isActiveStatus(shellStatus) || isTerminalStatus(s1)) {
            // 种子已终态但壳未滚完：仍算进行中，直到壳终态才 100
            return new StageProgress(stages, isTerminalStatus(shellStatus) ? 2 : 1);
        }
        return new StageProgress(stages, 0);
    }

    /** 2.线索发现：开始 → Maigret → 网页检索 → 完成 */
    private StageProgress stageDiscovery(Map<String, Object> shell, String shellStatus) {
        List<String> stages = Arrays.asList("开始", "Maigret", "网页检索", "完成");
        if (isTerminalStatus(shellStatus)) {
            return new StageProgress(stages, 3);
        }
        String s2 = findDescendantStatus(shell, "step2_maigret");
        String s3 = findDescendantStatus(shell, "step3_web_search");
        if (isTerminalStatus(s3)) {
            return new StageProgress(stages, 3);
        }
        if (isActiveStatus(s3) || isTerminalStatus(s2)) {
            return new StageProgress(stages, 2);
        }
        if (isActiveStatus(s2) || isActiveStatus(shellStatus)) {
            return new StageProgress(stages, 1);
        }
        return new StageProgress(stages, 0);
    }

    /** 3.账号采集：开始 → 主页采集中 → 完成 */
    private StageProgress stageAccountCollect(Map<String, Object> shell, String shellStatus) {
        List<String> stages = Arrays.asList("开始", "主页采集中", "完成");
        if (isTerminalStatus(shellStatus)) {
            return new StageProgress(stages, 2);
        }
        String s4 = findDescendantStatus(shell, "step4_profiles");
        if (isActiveStatus(s4) || isActiveStatus(shellStatus)
                || hasActiveDescendant(shell)
                || isTerminalStatus(s4)) {
            return new StageProgress(stages, 1);
        }
        return new StageProgress(stages, 0);
    }

    /** 4.关联碰撞：开始 → 4.1 → 4.2 → 4.3 → 完成 */
    private StageProgress stageCollision(Map<String, Object> shell, String shellStatus) {
        List<String> stages = Arrays.asList("开始", "4.1信息核验", "4.2认定", "4.3社工库", "完成");
        if (isTerminalStatus(shellStatus)) {
            return new StageProgress(stages, 4);
        }
        String s5 = findDescendantStatus(shell, "step5_streams");
        String s6 = findDescendantStatus(shell, "step6_validated");
        String s43 = findDescendantStatus(shell, "step6_osint_es");
        if (isTerminalStatus(s43)) {
            return new StageProgress(stages, 4);
        }
        if (isActiveStatus(s43) || isTerminalStatus(s6)) {
            return new StageProgress(stages, 3);
        }
        if (isActiveStatus(s6) || isTerminalStatus(s5)) {
            return new StageProgress(stages, 2);
        }
        if (isActiveStatus(s5) || isActiveStatus(shellStatus)) {
            return new StageProgress(stages, 1);
        }
        return new StageProgress(stages, 0);
    }

    /** 5.内容采集：开始 → 发文采集中 → 完成 */
    private StageProgress stageContent(Map<String, Object> shell, String shellStatus) {
        List<String> stages = Arrays.asList("开始", "发文采集中", "完成");
        if (isTerminalStatus(shellStatus)) {
            return new StageProgress(stages, 2);
        }
        String s7 = findDescendantStatus(shell, "step7_posts");
        if (isActiveStatus(s7) || isActiveStatus(shellStatus)
                || hasActiveDescendant(shell)
                || isTerminalStatus(s7)) {
            return new StageProgress(stages, 1);
        }
        return new StageProgress(stages, 0);
    }

    /** 6.深度研判：开始 → 图片分析 → 观点分析 → PII圈层 → 完成 */
    private StageProgress stageAnalysis(Map<String, Object> shell, String shellStatus) {
        List<String> stages = Arrays.asList("开始", "图片分析", "观点分析", "PII圈层", "完成");
        if (isTerminalStatus(shellStatus)) {
            return new StageProgress(stages, 4);
        }
        String s8 = findDescendantStatus(shell, "step8_img_analysis");
        String s9 = findDescendantStatus(shell, "step9_context_views");
        String s10 = findDescendantStatus(shell, "step10_context_pii");
        if (isTerminalStatus(s10)) {
            return new StageProgress(stages, 4);
        }
        if (isActiveStatus(s10) || isTerminalStatus(s9)) {
            return new StageProgress(stages, 3);
        }
        if (isActiveStatus(s9) || isTerminalStatus(s8)) {
            return new StageProgress(stages, 2);
        }
        if (isActiveStatus(s8) || isActiveStatus(shellStatus)) {
            return new StageProgress(stages, 1);
        }
        return new StageProgress(stages, 0);
    }

    /** 7.报告生成：开始 → 写报中 → 完成 */
    private StageProgress stageReport(Map<String, Object> shell, String shellStatus) {
        List<String> stages = Arrays.asList("开始", "写报中", "完成");
        if (isTerminalStatus(shellStatus)) {
            return new StageProgress(stages, 2);
        }
        String s11 = findDescendantStatus(shell, "step11_report");
        if (isActiveStatus(s11) || isActiveStatus(shellStatus) || isTerminalStatus(s11)) {
            return new StageProgress(stages, 1);
        }
        return new StageProgress(stages, 0);
    }

    private boolean isTerminalStatus(String status) {
        return TERMINAL_STATUSES.contains(stringVal(status));
    }

    private boolean isActiveStatus(String status) {
        return "running".equals(stringVal(status));
    }

    /** 在壳子树中查找业务步 status；找不到返回空串。 */
    private String findDescendantStatus(Map<String, Object> root, String stepKey) {
        Map<String, Object> hit = findDescendantNode(root, stepKey);
        if (hit == null) {
            return "";
        }
        return stringVal(hit.get("status"));
    }

    private Map<String, Object> findDescendantNode(Map<String, Object> root, String stepKey) {
        if (root == null || stepKey == null || stepKey.isEmpty()) {
            return null;
        }
        if (stepKey.equals(stringVal(root.get("stepKey")))) {
            return root;
        }
        for (Map<String, Object> child : childrenOf(root)) {
            Map<String, Object> hit = findDescendantNode(child, stepKey);
            if (hit != null) {
                return hit;
            }
        }
        return null;
    }

    private boolean hasActiveDescendant(Map<String, Object> root) {
        for (Map<String, Object> child : childrenOf(root)) {
            if (isActiveStatus(stringVal(child.get("status")))) {
                return true;
            }
            if (hasActiveDescendant(child)) {
                return true;
            }
        }
        return false;
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
        // 路线 C 预留：节点下 live 摘要，P0 由前端本地算，后端先返 null
        node.put("thoughtPreview", null);
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
                || "mcp_bilibili_get_user_info".equals(toolName)
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
