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

    /** 步骤详情里单张图片嵌入 dataUrl 上限 */
    private static final int STEP_IMAGE_MAX_BYTES = 1536 * 1024;

    @Autowired
    private VideoAssetQueryService videoAssetQueryService;

    @Autowired
    private ImageAssetQueryService imageAssetQueryService;

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
        } else if ("step5_stream_text".equals(stepKey) || "step5_streams".equals(stepKey)) {
            // 4.1 / 4.1.1：直接返回身份流比对明细（非仅结论摘要）
            String streamType = "step5_stream_text".equals(stepKey) ? "text" : "";
            records = loadIdentityStreamRecords(taskId, streamType);
            if (records.isEmpty()) {
                records = loadDisplayRecords(taskId, stepKey);
            }
        } else if ("collect_images".equals(dataType)) {
            records = imageAssetQueryService.listTaskImagesWithDataUrl(
                    taskId, 100, STEP_IMAGE_MAX_BYTES);
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
        // 4.1.1：模型推理结论 + 规则摘要；并把结论前置到 records 便于前端直接渲染
        if ("step5_stream_text".equals(stepKey)) {
            Map<String, Object> payload = parsePayloadMap(step.get("payload_json"));
            if (!payload.isEmpty()) {
                out.put("summary", payload);
            }
            String modelAnalysis = firstNonEmpty(
                    stringVal(payload.get("modelAnalysis")),
                    stringVal(payload.get("conclusion")),
                    stringVal(step.get("message")));
            out.put("modelAnalysis", modelAnalysis);
            if (!modelAnalysis.isEmpty()) {
                List<Map<String, Object>> withConclusion = new ArrayList<Map<String, Object>>();
                Map<String, Object> head = new LinkedHashMap<String, Object>();
                head.put("recordKind", "model_conclusion");
                head.put("conclusion", modelAnalysis);
                head.put("modelAnalysis", modelAnalysis);
                head.put("source", payload.get("source"));
                head.put("matched", payload.get("matched"));
                head.put("total", payload.get("total"));
                withConclusion.add(head);
                withConclusion.addAll(records);
                records = withConclusion;
                out.put("records", records);
                out.put("recordCount", Integer.valueOf(records.size()));
            }
        }
        // 4.1.2：图片资产 + 分析结果 + 图片流对比结果
        if ("step5_stream_image".equals(stepKey) && "collect_images".equals(dataType)) {
            Map<String, Object> payload = parsePayloadMap(step.get("payload_json"));
            List<Map<String, Object>> streamRows = loadIdentityStreamRecords(taskId, "image");
            out.put("streamRecordCount", Integer.valueOf(streamRows.size()));
            out.put("streamRecords", streamRows);
            String compareConclusion = firstNonEmpty(
                    stringVal(payload.get("compareConclusion")),
                    stringVal(payload.get("modelAnalysis")));
            if (compareConclusion.isEmpty() && payload.get("compare") instanceof Map) {
                @SuppressWarnings("unchecked")
                Map<String, Object> cmp = (Map<String, Object>) payload.get("compare");
                compareConclusion = firstNonEmpty(
                        stringVal(cmp.get("conclusion")),
                        stringVal(cmp.get("modelAnalysis")));
            }
            out.put("compareConclusion", compareConclusion);
            out.put("modelAnalysis", compareConclusion);
            if (!payload.isEmpty()) {
                out.put("summary", payload);
            }
        }
        return out;
    }

    private static String firstNonEmpty(String... values) {
        if (values == null) {
            return "";
        }
        for (String v : values) {
            if (v != null && !v.trim().isEmpty()) {
                return v.trim();
            }
        }
        return "";
    }

    /** 身份流比对明细（collect_identity_streams） */
    private List<Map<String, Object>> loadIdentityStreamRecords(String taskId, String streamType) {
        List<Map<String, Object>> rows;
        if (streamType == null || streamType.isEmpty()) {
            rows = collectTaskMapper.selectIdentityStreams(taskId);
        } else {
            rows = collectTaskMapper.selectIdentityStreamsByType(taskId, streamType);
        }
        if (rows == null || rows.isEmpty()) {
            return new ArrayList<Map<String, Object>>();
        }
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> row : rows) {
            Map<String, Object> item = new LinkedHashMap<String, Object>();
            item.put("streamId", row.get("stream_id"));
            item.put("streamType", row.get("stream_type"));
            item.put("platform", row.get("source_platform"));
            item.put("accountId", row.get("source_account_id"));
            item.put("sourceField", row.get("source_field"));
            item.put("payloadText", row.get("payload_text"));
            item.put("payloadUrl", row.get("payload_url"));
            item.put("validationStatus", row.get("validation_status"));
            item.put("validationDetail", row.get("validation_detail"));
            item.put("createdAt", row.get("created_at"));
            item.put("updatedAt", row.get("updated_at"));
            out.add(item);
        }
        return out;
    }

    private Map<String, Object> parsePayloadMap(Object raw) {
        if (raw == null) {
            return new LinkedHashMap<String, Object>();
        }
        if (raw instanceof Map) {
            @SuppressWarnings("unchecked")
            Map<String, Object> m = (Map<String, Object>) raw;
            return m;
        }
        if (raw instanceof String) {
            String text = ((String) raw).trim();
            if (text.isEmpty()) {
                return new LinkedHashMap<String, Object>();
            }
            try {
                Object parsed = JSON.parse(text);
                if (parsed instanceof Map) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> m = (Map<String, Object>) parsed;
                    return m;
                }
            } catch (Exception ignore) {
                // ignore
            }
        }
        return new LinkedHashMap<String, Object>();
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
        if ("step5_stream_text".equals(stepKey)) {
            // 文本流规则/模型比对明细（collect_identity_streams）
            return "collect_identity_streams";
        }
        if ("step5_stream_image".equals(stepKey)) {
            // 图片资产（含 visionText）；streamRecords 另附身份流
            return "collect_images";
        }
        if ("step5_streams".equals(stepKey)) {
            return "collect_identity_streams";
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
