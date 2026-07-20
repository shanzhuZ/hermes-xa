package com.example.aw.collect.service;

import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * 自定义任务动态流程图服务。
 * <p>
 * 保证「图上节点」与「可执行步骤」一致：业务动作前须 begin_step，
 * 结束须 finish_step；计划变更用 upsert（支持 merge / replace，可追加节点）。
 */
@Service
public class FlowStepService {

    private static final Pattern STEP_KEY_OK = Pattern.compile("^[a-zA-Z][a-zA-Z0-9_]{0,62}$");
    private static final Set<String> FINISH_OK = new HashSet<String>();

    static {
        FINISH_OK.add("completed");
        FINISH_OK.add("failed");
        FINISH_OK.add("skipped");
    }

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /**
     * 批量写入/更新步骤元数据。
     *
     * @param mode merge=只 upsert 传入步骤；replace=删掉未出现在列表中的步骤后再 upsert
     */
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> upsertSteps(String taskId, String mode, List<Map<String, Object>> steps) {
        String tid = requireTask(taskId);
        String m = mode == null || mode.trim().isEmpty() ? "merge" : mode.trim().toLowerCase(Locale.ROOT);
        if (!"merge".equals(m) && !"replace".equals(m)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid_mode");
        }
        if (steps == null || steps.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "steps_required");
        }

        List<String> keys = new ArrayList<String>();
        int order = 0;
        for (Map<String, Object> raw : steps) {
            if (raw == null) {
                continue;
            }
            String stepKey = str(raw.get("stepKey"));
            if (stepKey.isEmpty()) {
                stepKey = str(raw.get("step_key"));
            }
            if (!STEP_KEY_OK.matcher(stepKey).matches()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid_stepKey:" + stepKey);
            }
            String title = str(raw.get("title"));
            if (title.isEmpty()) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "title_required:" + stepKey);
            }
            if (title.length() > 256) {
                title = title.substring(0, 256);
            }
            String parent = str(raw.get("parentStepKey"));
            if (parent.isEmpty()) {
                parent = str(raw.get("parent_step_key"));
            }
            if (parent.isEmpty()) {
                parent = null;
            }
            int stepOrder = toInt(raw.get("stepOrder"), toInt(raw.get("step_order"), (order + 1) * 10));
            String stepNode = str(raw.get("stepNode"));
            if (stepNode.isEmpty()) {
                stepNode = str(raw.get("step_node"));
            }
            if (stepNode.isEmpty()) {
                stepNode = String.valueOf(order + 1);
            }
            collectTaskMapper.upsertPhaseStepMeta(tid, stepKey, parent, stepOrder, stepNode, title);
            String status = str(raw.get("status"));
            if (!status.isEmpty()) {
                String msg = str(raw.get("message"));
                if (msg.isEmpty()) {
                    msg = null;
                }
                applyStatus(tid, stepKey, status, msg);
            }
            keys.add(stepKey);
            order++;
        }
        if (keys.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "steps_required");
        }
        if ("replace".equals(m)) {
            collectTaskMapper.deletePhaseStepsNotIn(tid, keys);
        }

        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("ok", Boolean.TRUE);
        body.put("taskId", tid);
        body.put("mode", m);
        body.put("upserted", Integer.valueOf(keys.size()));
        body.put("stepKeys", keys);
        return body;
    }

    /** 开始执行某步骤：status → running（不存在则 404） */
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> beginStep(String taskId, String stepKey, String message) {
        String tid = requireTask(taskId);
        String key = requireStepKey(stepKey);
        ensureStepExists(tid, key);
        String msg = message == null || message.trim().isEmpty() ? "执行中" : message.trim();
        if (msg.length() > 2000) {
            msg = msg.substring(0, 2000);
        }
        collectTaskMapper.updatePhaseStepLifecycle(tid, key, "running", msg);
        return statusBody(tid, key, "running", msg);
    }

    /** 结束某步骤：completed / failed / skipped */
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> finishStep(String taskId, String stepKey, String status, String message) {
        String tid = requireTask(taskId);
        String key = requireStepKey(stepKey);
        ensureStepExists(tid, key);
        String st = status == null ? "" : status.trim().toLowerCase(Locale.ROOT);
        if (!FINISH_OK.contains(st)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid_finish_status");
        }
        String msg = message == null || message.trim().isEmpty() ? st : message.trim();
        if (msg.length() > 2000) {
            msg = msg.substring(0, 2000);
        }
        collectTaskMapper.updatePhaseStepLifecycle(tid, key, st, msg);
        return statusBody(tid, key, st, msg);
    }

    /**
     * 自定义任务终稿兜底：模型若未调 flow，仍保证步骤树与任务状态可收口。
     * <ul>
     *   <li>若只有 step_plan：补 synthesize「汇总结论」并 completed</li>
     *   <li>step_plan 仍为 pending/running → completed</li>
     *   <li>其余仍 running/pending 的步骤 → completed（标明终稿兜底）</li>
     *   <li>hermes_tasks → completed</li>
     * </ul>
     */
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> reconcileOnFinalAnswer(String taskId) {
        String tid = requireTask(taskId);
        List<Map<String, Object>> steps = collectTaskMapper.selectPhaseSteps(tid);
        int businessCount = 0;
        if (steps != null) {
            for (Map<String, Object> row : steps) {
                String key = str(row.get("step_key"));
                if (!"step_plan".equals(key)) {
                    businessCount++;
                }
            }
        }
        if (businessCount == 0) {
            collectTaskMapper.upsertPhaseStepMeta(tid, "synthesize", null, 20, "2", "汇总结论");
            collectTaskMapper.updatePhaseStepLifecycle(tid, "synthesize", "completed", "终稿已生成（自动收口）");
        }
        String planStatus = collectTaskMapper.selectStepStatus(tid, "step_plan");
        if (planStatus != null && ("pending".equals(planStatus) || "running".equals(planStatus))) {
            collectTaskMapper.updatePhaseStepLifecycle(tid, "step_plan", "completed", "计划已完成（终稿兜底）");
        }
        List<Map<String, Object>> after = collectTaskMapper.selectPhaseSteps(tid);
        if (after != null) {
            for (Map<String, Object> row : after) {
                String key = str(row.get("step_key"));
                String st = str(row.get("status"));
                if ("running".equals(st) || "pending".equals(st)) {
                    collectTaskMapper.updatePhaseStepLifecycle(tid, key, "completed", "终稿兜底收口");
                }
            }
        }
        collectTaskMapper.markTaskCompleted(tid, "done");
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("ok", Boolean.TRUE);
        body.put("taskId", tid);
        body.put("reconciled", Boolean.TRUE);
        return body;
    }

    private void applyStatus(String tid, String stepKey, String status, String message) {
        String st = status.trim().toLowerCase(Locale.ROOT);
        if ("pending".equals(st) || "running".equals(st) || FINISH_OK.contains(st)) {
            collectTaskMapper.updatePhaseStepLifecycle(tid, stepKey, st, message);
        } else {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid_status:" + st);
        }
    }

    private String requireTask(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "taskId_required");
        }
        String tid = taskId.trim();
        Map<String, Object> task = collectTaskMapper.selectTaskById(tid);
        if (task == null || task.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "task_not_found");
        }
        return tid;
    }

    private static String requireStepKey(String stepKey) {
        String key = stepKey == null ? "" : stepKey.trim();
        if (!STEP_KEY_OK.matcher(key).matches()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid_stepKey");
        }
        return key;
    }

    private void ensureStepExists(String tid, String stepKey) {
        String cur = collectTaskMapper.selectStepStatus(tid, stepKey);
        if (cur == null) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "step_not_found");
        }
    }

    private static Map<String, Object> statusBody(String tid, String key, String status, String message) {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("ok", Boolean.TRUE);
        body.put("taskId", tid);
        body.put("stepKey", key);
        body.put("status", status);
        body.put("message", message);
        return body;
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
