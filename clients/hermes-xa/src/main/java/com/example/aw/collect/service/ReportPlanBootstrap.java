package com.example.aw.collect.service;

import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.stream.ThoughtStreamHub;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 04 写报：Java 推「思考中 → 规划七大节点」并收口 step_plan。
 * <p>
 * 分两段：{@link #beginPlanStream} 同步开场（便于立刻返回 taskId）；
 * {@link #finishPlanStreamPaced} 异步慢推几秒后再 {@code plan.ready}。
 */
@Service
public class ReportPlanBootstrap {

    private static final Logger log = LoggerFactory.getLogger(ReportPlanBootstrap.class);

    /** 先停在「思考中」的时长 */
    private static final long THINKING_HOLD_MS = 1800L;
    /** 再停在「规划中」的时长，随后列出 7 节点 */
    private static final long PLANNING_HOLD_MS = 1600L;

    @Autowired
    private ThoughtStreamHub thoughtStreamHub;

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /**
     * 同步：开流 + step_plan=running + 先推「思考中…」（不阻塞数秒）。
     */
    public void beginPlanStream(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        thoughtStreamHub.open(taskId);
        collectTaskMapper.updateStepStatus(
                taskId, ReportTaskCreateService.STEP_PLAN, "running", "思考中…");

        Map<String, Object> started = new LinkedHashMap<String, Object>();
        started.put("taskId", taskId);
        started.put("content", "思考中…");
        thoughtStreamHub.publish(taskId, "plan.started", started);

        // 累计文案：兼容前端用最新一条 _thinking 覆盖展示
        publishThinking(taskId, "思考中…");
        log.info("04 规划开场已推送（思考中） taskId={}", taskId);
    }

    /**
     * 异步慢推：思考中 → 规划中 → 列出 7 节点 → plan.ready。
     * 不含「说明」类附注；不点亮业务节点。
     * <p>
     * 关键：先落库收口 step_plan，再推 SSE。避免 SseEmitter.send 阻塞时第 0 步永久 running。
     */
    public void finishPlanStreamPaced(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return;
        }
        sleepQuiet(THINKING_HOLD_MS);

        String planning = "思考中…\n\n任务规划中…\n正在为本任务规划执行节点（共 7 个大阶段）…";
        publishThinkingSafe(taskId, planning);

        sleepQuiet(PLANNING_HOLD_MS);

        List<Map<String, Object>> phases = fixedPhases();
        StringBuilder listed = new StringBuilder();
        listed.append(planning).append("\n\n");
        listed.append("已规划好本任务 7 大节点：\n");
        for (Map<String, Object> p : phases) {
            listed.append(p.get("title")).append("\n");
        }
        listed.append("\n规划完成。");
        String listedText = listed.toString();

        String planMsg = "已规划好节点：1.锁定目标；2.线索发现；3.账号采集；4.关联碰撞；"
                + "5.内容采集；6.深度研判；7.报告生成";
        // 先收口 DB，再推流：树进度不依赖 SSE
        collectTaskMapper.updateStepStatus(
                taskId, ReportTaskCreateService.STEP_PLAN, "completed", planMsg);

        publishThinkingSafe(taskId, listedText);

        Map<String, Object> ready = new LinkedHashMap<String, Object>();
        ready.put("taskId", taskId);
        ready.put("phases", phases);
        ready.put("content", "已规划好七大执行阶段（待开始执行）");
        ready.put("gatePhase", null);
        ready.put("executionStarted", Boolean.FALSE);
        try {
            thoughtStreamHub.publish(taskId, "plan.ready", ready);
        } catch (Exception e) {
            log.warn("plan.ready 推送失败（step_plan 已 completed） taskId={}: {}", taskId, e.getMessage());
        }

        log.info("04 规划流慢推完成（业务节点仍 pending） taskId={}", taskId);
    }

    private void publishThinking(String taskId, String content) {
        Map<String, Object> delta = new LinkedHashMap<String, Object>();
        delta.put("taskId", taskId);
        delta.put("content", content);
        thoughtStreamHub.publish(taskId, "assistant.delta", delta);

        Map<String, Object> progress = new LinkedHashMap<String, Object>();
        progress.put("taskId", taskId);
        progress.put("content", content);
        progress.put("toolName", "_thinking");
        thoughtStreamHub.publish(taskId, "tool.progress", progress);
    }

    private void publishThinkingSafe(String taskId, String content) {
        try {
            publishThinking(taskId, content);
        } catch (Exception e) {
            log.warn("规划思考文案推送失败 taskId={}: {}", taskId, e.getMessage());
        }
    }

    private static void sleepQuiet(long ms) {
        if (ms <= 0L) {
            return;
        }
        try {
            Thread.sleep(ms);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private static List<Map<String, Object>> fixedPhases() {
        List<Map<String, Object>> phases = new ArrayList<Map<String, Object>>();
        phases.add(phase(1, ReportTaskCreateService.PHASE_LOCK_TARGET, "1. 锁定目标"));
        phases.add(phase(2, ReportTaskCreateService.PHASE_DISCOVERY, "2. 线索发现"));
        phases.add(phase(3, ReportTaskCreateService.PHASE_ACCOUNT_COLLECT, "3. 账号采集"));
        phases.add(phase(4, ReportTaskCreateService.PHASE_COLLISION, "4. 关联碰撞"));
        phases.add(phase(5, ReportTaskCreateService.PHASE_CONTENT, "5. 内容采集"));
        phases.add(phase(6, ReportTaskCreateService.PHASE_ANALYSIS, "6. 深度研判"));
        phases.add(phase(7, ReportTaskCreateService.PHASE_REPORT, "7. 报告生成"));
        return phases;
    }

    private static Map<String, Object> phase(int order, String key, String title) {
        Map<String, Object> m = new LinkedHashMap<String, Object>();
        m.put("order", Integer.valueOf(order));
        m.put("phaseKey", key);
        m.put("title", title);
        return m;
    }
}
