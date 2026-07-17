package com.example.aw.collect.stream;

import com.example.aw.collect.mapper.CollectTaskMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * 方案 C 左半边：根据 tool.started / tool.completed 粗写步骤状态。
 * <p>
 * Hook 仍为细状态真相源；本类异常不得打断 SSE。
 * <ul>
 *   <li>started → pending/running 升为 running</li>
 *   <li>completed 且白名单 → 可粗 completed；Apify/采集类禁止</li>
 * </ul>
 */
@Component
public class CoarseStepSync {

    private static final Logger log = LoggerFactory.getLogger(CoarseStepSync.class);

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    public void onToolEvent(String taskId, String eventType, String toolName, Boolean success) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        if (eventType == null) {
            return;
        }
        String et = eventType.trim();
        if (!"tool.started".equals(et) && !"tool.completed".equals(et) && !"tool.failed".equals(et)) {
            return;
        }
        try {
            Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
            if (task == null || task.isEmpty()) {
                return;
            }
            Object typeObj = task.get("task_type");
            String taskType = typeObj == null ? "" : String.valueOf(typeObj);
            CoarseToolStepMapping.Target target = CoarseToolStepMapping.resolve(taskType, toolName);
            if (target == null || target.stepKey == null) {
                return;
            }
            String stepKey = remapReportStepIfNeeded(taskType, taskId, toolName, target.stepKey);

            if ("tool.started".equals(et)) {
                String msg = "执行 " + (toolName == null ? "工具" : toolName);
                // verify：步骤4 禁止在步骤2(采集)/步骤3(风格)未完成时粗点亮（vision 抢跑）
                if (isVerifyStep4Blocked(taskType, taskId, stepKey)) {
                    log.debug("粗同步跳过 step4（前置未完成）taskId={} step={} tool={}",
                            taskId, stepKey, toolName);
                    return;
                }
                // report：步骤二、三完成前禁止粗点亮步骤4（种子 Apify get_dataset 易误挂 step4_profiles）
                if (isReportStep4Blocked(taskType, taskId, stepKey)) {
                    log.debug("粗同步跳过 step4（步骤2/3未完成）taskId={} step={} tool={}",
                            taskId, stepKey, toolName);
                    return;
                }
                // report：步骤6 完成前禁止粗点亮步骤7（模型抢跑发文工具）
                if (isReportStep7Blocked(taskType, taskId, stepKey)) {
                    log.debug("粗同步跳过 step7（步骤5/6未完成）taskId={} step={} tool={}",
                            taskId, stepKey, toolName);
                    return;
                }
                // 先父后子：父节点不得晚于子节点 running
                for (String parentKey : CoarseToolStepMapping.parentsToEnsureRunning(taskType, stepKey)) {
                    int pn = collectTaskMapper.updateStepStatusCoarseRunning(
                            taskId, parentKey, "子步骤执行中");
                    if (pn > 0) {
                        log.debug("粗同步父 running taskId={} parent={} for={}",
                                taskId, parentKey, stepKey);
                    }
                }
                int n = collectTaskMapper.updateStepStatusCoarseRunning(taskId, stepKey, msg);
                if (n > 0) {
                    log.debug("粗同步 running taskId={} step={} tool={}", taskId, stepKey, toolName);
                }
                return;
            }

            // tool.completed / tool.failed：仅白名单可粗写 completed；失败不在此改 failed（交 Hook）
            if (("tool.completed".equals(et) || "tool.failed".equals(et)) && target.allowCoarseComplete) {
                if ("tool.failed".equals(et) || Boolean.FALSE.equals(success)) {
                    return;
                }
                String msg = "工具完成 " + (toolName == null ? "" : toolName);
                int n = collectTaskMapper.updateStepStatusCoarseCompleted(taskId, stepKey, msg);
                if (n > 0) {
                    log.debug("粗同步 completed taskId={} step={} tool={}", taskId, stepKey, toolName);
                }
            }
        } catch (Exception e) {
            log.warn("粗同步失败 taskId={} event={} tool={}: {}", taskId, eventType, toolName, e.getMessage());
        }
    }

    /**
     * step4_* 粗 running 门禁：避免 Agent 提前 vision 把步骤4点亮。
     * <ul>
     *   <li>collect/expand/verify：须 step3_profiles=completed</li>
     *   <li>verify 额外须 step3_streams=completed（风格归纳完成后再进入文本/图片比对）</li>
     * </ul>
     */
    /**
     * account_report：按阶段改写粗同步目标。
     * <ul>
     *   <li>步骤一已完成后，主页 MCP 从 step1_seed 改写到 step4_profile_*</li>
     *   <li>步骤一种子轮 Apify：step4_profile_* 改写回 step1_seed</li>
     *   <li>步骤七进行中：Apify 从 step4_profile_* 改写到 step7_post_*</li>
     * </ul>
     */
    private String remapReportStepIfNeeded(String taskType, String taskId, String toolName, String stepKey) {
        if (!"account_report".equals(taskType) || stepKey == null) {
            return stepKey;
        }
        String platform = CoarseToolStepMapping.platformOfTool(toolName);
        String step1 = collectTaskMapper.selectStepStatus(taskId, "step1_seed");
        boolean step1Open = step1 == null
                || step1.isEmpty()
                || "pending".equals(step1)
                || "running".equals(step1);

        // 主页 MCP：步骤一完成后必须点亮 step4_profile_*（否则工具已跑、子节点仍 pending）
        if ("step1_seed".equals(stepKey) && platform != null && !platform.isEmpty() && !step1Open) {
            return "step4_profile_" + platform;
        }

        // 种子轮 Apify（含 get_actor_run / get_dataset_items）：步骤一未完成时一律挂 step1
        // get_dataset_items 无 platform，原先会落到 step4_profiles，造成步骤2未完就假 running
        if (step1Open
                && CoarseToolStepMapping.isApifySeedLikeTool(toolName)
                && ("step4_profiles".equals(stepKey) || stepKey.startsWith("step4_profile_"))) {
            return "step1_seed";
        }

        String step7 = collectTaskMapper.selectStepStatus(taskId, "step7_posts");
        return CoarseToolStepMapping.remapReportApifyIfStep7(stepKey, step7, platform);
    }

    private boolean isVerifyStep4Blocked(String taskType, String taskId, String stepKey) {
        if (stepKey == null || !stepKey.startsWith("step4_")) {
            return false;
        }
        boolean verify = "account_verify".equals(taskType);
        boolean collectOrExpand = "account_collect".equals(taskType) || "account_expand".equals(taskType);
        if (!verify && !collectOrExpand) {
            return false;
        }
        String profiles = collectTaskMapper.selectStepStatus(taskId, "step3_profiles");
        if (!"completed".equals(profiles)) {
            return true;
        }
        if (verify) {
            String streams = collectTaskMapper.selectStepStatus(taskId, "step3_streams");
            return !"completed".equals(streams);
        }
        return false;
    }

    /**
     * account_report：步骤二、三完成前禁止粗同步点亮 step4_*。
     * 典型误伤：种子 Facebook Apify 的 get_actor_run / get_dataset_items 默认映射 step4_profiles。
     */
    private boolean isReportStep4Blocked(String taskType, String taskId, String stepKey) {
        if (!"account_report".equals(taskType) || stepKey == null) {
            return false;
        }
        if (!"step4_profiles".equals(stepKey) && !stepKey.startsWith("step4_profile_")) {
            return false;
        }
        String s2 = collectTaskMapper.selectStepStatus(taskId, "step2_maigret");
        String s3 = collectTaskMapper.selectStepStatus(taskId, "step3_web_search");
        boolean s2ok = "completed".equals(s2) || "skipped".equals(s2);
        boolean s3ok = "completed".equals(s3) || "skipped".equals(s3);
        return !(s2ok && s3ok);
    }

    /**
     * account_report：步骤六完成前禁止粗同步点亮 step7_*。
     * 否则模型在步骤5 vision 未完时调发文工具，UI 会显示步骤7 running、子节点未物化。
     */
    private boolean isReportStep7Blocked(String taskType, String taskId, String stepKey) {
        if (!"account_report".equals(taskType) || stepKey == null) {
            return false;
        }
        if (!"step7_posts".equals(stepKey) && !stepKey.startsWith("step7_post_")) {
            return false;
        }
        String s6 = collectTaskMapper.selectStepStatus(taskId, "step6_validated");
        return !"completed".equals(s6);
    }
}
