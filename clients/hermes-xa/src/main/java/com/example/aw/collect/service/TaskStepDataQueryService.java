package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.example.aw.collect.mapper.CollectTaskMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 按采集步骤查询 MySQL 业务表明细，供前端点击步骤后展示库中内容。
 */
@Service
public class TaskStepDataQueryService {

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /**
     * 查询某一步骤对应的业务表数据。
     */
    public Map<String, Object> getStepData(String taskId, String stepKey) {
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId);
        if (task == null || task.isEmpty()) {
            return notFound("task_not_found", taskId, stepKey);
        }

        Map<String, Object> step = collectTaskMapper.selectPhaseStep(taskId, stepKey);
        if (step == null || step.isEmpty()) {
            return notFound("step_not_found", taskId, stepKey);
        }

        String status = stringVal(step.get("status"));
        List<Map<String, Object>> records = loadRecords(taskId, stepKey);
        String dataType = resolveDataType(stepKey);

        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("taskId", taskId);
        out.put("stepKey", stepKey);
        out.put("title", step.get("title"));
        out.put("status", status);
        out.put("statusLabel", statusLabel(status));
        out.put("message", step.get("message"));
        out.put("dataType", dataType);
        out.put("recordCount", records.size());
        out.put("records", records);
        return out;
    }

    /**
     * 根据 stepKey 决定查哪张业务表（与 01 采集流水线步骤一一对应）。
     */
    private List<Map<String, Object>> loadRecords(String taskId, String stepKey) {
        if ("step1_seed".equals(stepKey)) {
            String platform = seedPlatform(taskId);
            if (platform != null && platform.length() > 0) {
                List<Map<String, Object>> rows = collectTaskMapper.selectProfilesByTaskAndPlatform(taskId, platform);
                if (rows != null && !rows.isEmpty()) {
                    return rows;
                }
            }
            List<Map<String, Object>> all = collectTaskMapper.selectProfilesByTaskId(taskId);
            return all != null ? all : new ArrayList<Map<String, Object>>();
        }
        if ("step2_cross_platform".equals(stepKey)) {
            List<Map<String, Object>> rows = collectTaskMapper.selectCrossPlatformCandidates(taskId);
            return rows != null ? rows : new ArrayList<Map<String, Object>>();
        }
        if ("step3_profiles".equals(stepKey)) {
            List<Map<String, Object>> rows = collectTaskMapper.selectProfilesByTaskId(taskId);
            return rows != null ? rows : new ArrayList<Map<String, Object>>();
        }
        if ("step3_streams".equals(stepKey)) {
            List<Map<String, Object>> rows = collectTaskMapper.selectIdentityStreams(taskId);
            return rows != null ? rows : new ArrayList<Map<String, Object>>();
        }
        if ("step4_text_compare".equals(stepKey)) {
            List<Map<String, Object>> rows = collectTaskMapper.selectIdentityStreamsByType(taskId, "text");
            return rows != null ? rows : new ArrayList<Map<String, Object>>();
        }
        if ("step4_image_compare".equals(stepKey)) {
            List<Map<String, Object>> rows = collectTaskMapper.selectIdentityStreamsByType(taskId, "image");
            return rows != null ? rows : new ArrayList<Map<String, Object>>();
        }
        if ("step5_validated".equals(stepKey)) {
            List<Map<String, Object>> rows = collectTaskMapper.selectValidatedAccounts(taskId);
            return rows != null ? rows : new ArrayList<Map<String, Object>>();
        }
        if ("step6_posts".equals(stepKey)) {
            List<Map<String, Object>> rows = collectTaskMapper.selectPostsByTaskId(taskId);
            return rows != null ? rows : new ArrayList<Map<String, Object>>();
        }
        if (stepKey.startsWith("step6_post_")) {
            String platform = stepKey.substring("step6_post_".length());
            List<Map<String, Object>> rows = collectTaskMapper.selectPostsByTaskAndPlatform(taskId, platform);
            return rows != null ? rows : new ArrayList<Map<String, Object>>();
        }
        return new ArrayList<Map<String, Object>>();
    }

    /**
     * 从 hermes_tasks.seed_json 里取种子平台，步骤一优先展示该平台资料。
     */
    private String seedPlatform(String taskId) {
        Map<String, Object> row = collectTaskMapper.selectTaskSeedJson(taskId);
        if (row == null || row.get("seed_json") == null) {
            return null;
        }
        Object raw = row.get("seed_json");
        try {
            JSONObject obj;
            if (raw instanceof String) {
                obj = JSON.parseObject((String) raw);
            } else {
                obj = JSON.parseObject(JSON.toJSONString(raw));
            }
            if (obj == null) {
                return null;
            }
            return obj.getString("platform");
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * 告诉前端当前 records 来自哪类业务数据，便于选择展示组件。
     */
    private String resolveDataType(String stepKey) {
        if ("step1_seed".equals(stepKey) || "step3_profiles".equals(stepKey)) {
            return "collect_profiles";
        }
        if ("step2_cross_platform".equals(stepKey)) {
            return "cross_platform_candidates";
        }
        if ("step3_streams".equals(stepKey) || "step4_text_compare".equals(stepKey)
                || "step4_image_compare".equals(stepKey)) {
            return "collect_identity_streams";
        }
        if ("step5_validated".equals(stepKey)) {
            return "collect_validated_accounts";
        }
        if ("step6_posts".equals(stepKey) || stepKey.startsWith("step6_post_")) {
            return "collect_posts";
        }
        return "unknown";
    }

    private Map<String, Object> notFound(String error, String taskId, String stepKey) {
        Map<String, Object> err = new LinkedHashMap<String, Object>();
        err.put("error", error);
        err.put("taskId", taskId);
        err.put("stepKey", stepKey);
        return err;
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

    private String stringVal(Object o) {
        return o == null ? "" : String.valueOf(o);
    }
}
