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

            if ("tool.started".equals(et)) {
                String msg = "执行 " + (toolName == null ? "工具" : toolName);
                // verify：步骤4 禁止在步骤2(采集)/步骤3(风格)未完成时粗点亮（vision 抢跑）
                if (isVerifyStep4Blocked(taskType, taskId, target.stepKey)) {
                    log.debug("粗同步跳过 step4（前置未完成）taskId={} step={} tool={}",
                            taskId, target.stepKey, toolName);
                    return;
                }
                // 先父后子：父节点不得晚于子节点 running
                for (String parentKey : CoarseToolStepMapping.parentsToEnsureRunning(taskType, target.stepKey)) {
                    int pn = collectTaskMapper.updateStepStatusCoarseRunning(
                            taskId, parentKey, "子步骤执行中");
                    if (pn > 0) {
                        log.debug("粗同步父 running taskId={} parent={} for={}",
                                taskId, parentKey, target.stepKey);
                    }
                }
                int n = collectTaskMapper.updateStepStatusCoarseRunning(taskId, target.stepKey, msg);
                if (n > 0) {
                    log.debug("粗同步 running taskId={} step={} tool={}", taskId, target.stepKey, toolName);
                }
                return;
            }

            // tool.completed / tool.failed：仅白名单可粗写 completed；失败不在此改 failed（交 Hook）
            if (("tool.completed".equals(et) || "tool.failed".equals(et)) && target.allowCoarseComplete) {
                if ("tool.failed".equals(et) || Boolean.FALSE.equals(success)) {
                    return;
                }
                String msg = "工具完成 " + (toolName == null ? "" : toolName);
                int n = collectTaskMapper.updateStepStatusCoarseCompleted(taskId, target.stepKey, msg);
                if (n > 0) {
                    log.debug("粗同步 completed taskId={} step={} tool={}", taskId, target.stepKey, toolName);
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
}
