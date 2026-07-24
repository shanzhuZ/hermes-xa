package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
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

    /** 步骤详情里单帧嵌入 dataUrl 上限（约 1.5MB） */
    private static final int STEP_FRAME_MAX_BYTES = 1536 * 1024;

    @Autowired
    private CollectTaskMapper collectTaskMapper;

    @Autowired
    private VideoAssetQueryService videoAssetQueryService;

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
        String dataType = resolveDataType(stepKey);

        List<Map<String, Object>> records;
        if ("collect_videos".equals(dataType)) {
            String platform = resolveVideoPlatform(stepKey);
            records = videoAssetQueryService.listTaskVideosWithFrames(taskId, platform, STEP_FRAME_MAX_BYTES);
        } else {
            records = loadDisplayRecords(taskId, stepKey);
        }

        Map<String, Object> out = new LinkedHashMap<String, Object>();
        out.put("taskId", taskId);
        out.put("stepKey", stepKey);
        out.put("title", step.get("title"));
        out.put("status", status);
        out.put("statusLabel", statusLabel(status));
        out.put("message", step.get("message"));
        out.put("dataType", dataType);
        out.put("recordCount", Integer.valueOf(records.size()));
        out.put("records", records);
        return out;
    }

    /**
     * 按 step_key 查询展示层 records（统一 [{label,value}] 结构）。
     */
    private List<Map<String, Object>> loadDisplayRecords(String taskId, String stepKey) {
        List<Map<String, Object>> rows = collectTaskMapper.selectDisplayRecordsByStepKey(taskId, stepKey);
        if (rows == null || rows.isEmpty()) {
            return new ArrayList<Map<String, Object>>();
        }
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> row : rows) {
            Map<String, Object> item = new LinkedHashMap<String, Object>();
            item.put("id", row.get("id"));
            item.put("recordTitle", row.get("record_title"));
            item.put("platform", row.get("platform"));
            item.put("accountId", row.get("account_id"));
            item.put("fields", parseDisplayFields(row.get("display_fields")));
            out.add(item);
        }
        return out;
    }

    private Object parseDisplayFields(Object raw) {
        if (raw == null) {
            return new ArrayList<Object>();
        }
        if (raw instanceof String) {
            String text = ((String) raw).trim();
            if (text.isEmpty()) {
                return new ArrayList<Object>();
            }
            try {
                return JSON.parse(text);
            } catch (Exception e) {
                return new ArrayList<Object>();
            }
        }
        return raw;
    }

    /**
     * 告诉前端当前 records 来自哪类业务数据，便于选择展示组件。
     */
    private String resolveDataType(String stepKey) {
        if (stepKey == null) {
            return "unknown";
        }
        if (stepKey.startsWith("step6_video_") || stepKey.startsWith("step7_video_")) {
            return "collect_videos";
        }
        if ("step1_input_accounts".equals(stepKey)) {
            return "input_accounts";
        }
        if ("step1_seed".equals(stepKey) || "step3_profiles".equals(stepKey)
                || (stepKey.startsWith("step3_profile_") && !"step3_profiles".equals(stepKey))) {
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
        if (stepKey.startsWith("step3_post_") || "step6_posts".equals(stepKey) || stepKey.startsWith("step6_post_")
                || "step7_posts".equals(stepKey) || stepKey.startsWith("step7_post_")) {
            return "collect_posts";
        }
        return "unknown";
    }

    /** step6_video_twitter / step7_video_youtube → platform */
    private static String resolveVideoPlatform(String stepKey) {
        if (stepKey == null) {
            return "";
        }
        if (stepKey.startsWith("step7_video_")) {
            return stepKey.substring("step7_video_".length());
        }
        if (stepKey.startsWith("step6_video_")) {
            return stepKey.substring("step6_video_".length());
        }
        return "";
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
