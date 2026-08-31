package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.util.EscapedTextDecoder;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.sql.Timestamp;
import java.text.SimpleDateFormat;
import java.util.Date;

/**
 * 按采集步骤查询 MySQL 业务表明细，供前端点击步骤后展示库中内容。
 */
@Service
public class TaskStepDataQueryService {

    /** 步骤详情里单帧嵌入 dataUrl 上限（约 1.5MB） */
    private static final int STEP_FRAME_MAX_BYTES = 1536 * 1024;

    /** 这 4 个节点详情只查 collect_step_demo_records，不查业务表 */
    private static final Set<String> DEMO_STEP_KEYS = new HashSet<String>(Arrays.asList(
            "step6_osint_es",
            "step6_geo_verify",
            "step6_rumor_sx",
            "step6_relation_graph"
    ));

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
        if (DEMO_STEP_KEYS.contains(stepKey)) {
            // 演示节点：只查 collect_step_demo_records
            records = loadDemoRecords(stepKey);
        } else if ("collect_videos".equals(dataType)) {
            String platform = resolveVideoPlatform(stepKey);
            records = videoAssetQueryService.listTaskVideosWithFrames(taskId, platform, STEP_FRAME_MAX_BYTES);
        } else if ("step5_stream_text".equals(stepKey) || "step5_streams".equals(stepKey)) {
            // 4.1 / 4.1.1：直接返回身份流比对明细（非仅结论摘要）
            String streamType = "step5_stream_text".equals(stepKey) ? "text" : "";
            records = loadIdentityStreamRecords(taskId, streamType);
            if (records.isEmpty()) {
                records = loadDisplayRecords(taskId, stepKey);
            }
        } else if ("collect_osint_hits".equals(dataType)) {
            records = loadOsintHitRecords(taskId);
            if (records.isEmpty()) {
                records = loadDisplayRecords(taskId, stepKey);
            }
        } else if ("collect_images".equals(dataType)) {
            records = imageAssetQueryService.listTaskImagesWithDataUrl(
                    taskId, 100, STEP_IMAGE_MAX_BYTES);
        } else {
            records = loadDisplayRecords(taskId, stepKey);
            if ("step9_context_views".equals(stepKey)) {
                records = enrichStep9PostEvidence(taskId, records);
            }
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
        // 6.1：图片流 Agent 分析（collect_images + 步骤 payload）
        if ("step8_img_analysis".equals(stepKey) && "collect_images".equals(dataType)) {
            Map<String, Object> payload = parsePayloadMap(step.get("payload_json"));
            if (!payload.isEmpty()) {
                out.put("summary", payload);
            }
            String msg = stringVal(step.get("message"));
            if (!msg.isEmpty()) {
                out.put("modelAnalysis", msg);
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

    /** 4.3 社工库命中明细 */
    private List<Map<String, Object>> loadOsintHitRecords(String taskId) {
        List<Map<String, Object>> rows = collectTaskMapper.selectOsintHits(taskId);
        if (rows == null || rows.isEmpty()) {
            return new ArrayList<Map<String, Object>>();
        }
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> row : rows) {
            Map<String, Object> item = new LinkedHashMap<String, Object>();
            item.put("id", row.get("id"));
            item.put("platform", row.get("platform"));
            item.put("accountId", row.get("account_id"));
            item.put("profileUrl", row.get("profile_url"));
            item.put("sourceIndex", row.get("source_index"));
            item.put("queryText", row.get("query_text"));
            item.put("hitCount", row.get("hit_count"));
            item.put("hitJson", row.get("hit_json"));
            item.put("toolOutputId", row.get("tool_output_id"));
            item.put("createdAt", row.get("created_at"));
            out.add(item);
        }
        return out;
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
     * 演示假数据：按 step_key 查 collect_step_demo_records（全局，与 task 无关）。
     */
    private List<Map<String, Object>> loadDemoRecords(String stepKey) {
        List<Map<String, Object>> rows = collectTaskMapper.selectDemoRecordsByStepKey(stepKey);
        if (rows == null || rows.isEmpty()) {
            return new ArrayList<Map<String, Object>>();
        }
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> row : rows) {
            Map<String, Object> item = new LinkedHashMap<String, Object>();
            item.put("id", row.get("id"));
            item.put("recordTitle", row.get("record_title"));
            item.put("accountId", row.get("account_id"));
            item.put("fields", parseDisplayFields(row.get("display_fields")));
            out.add(item);
        }
        return out;
    }

    /**
     * 按 step_key 查询展示层 records（统一 [{label,value}] 结构）。
     * 04 发文步骤是 step7_post_*，但展示层沿用 01 的 step6_post_* 写入，查不到时回退。
     */
    private List<Map<String, Object>> loadDisplayRecords(String taskId, String stepKey) {
        List<Map<String, Object>> rows = collectTaskMapper.selectDisplayRecordsByStepKey(taskId, stepKey);
        if ((rows == null || rows.isEmpty()) && stepKey != null) {
            if (stepKey.startsWith("step7_post_")) {
                String fallback = "step6_post_" + stepKey.substring("step7_post_".length());
                rows = collectTaskMapper.selectDisplayRecordsByStepKey(taskId, fallback);
            } else if ("step7_posts".equals(stepKey)) {
                rows = collectTaskMapper.selectDisplayRecordsByStepKey(taskId, "step6_posts");
            }
        }
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
                return EscapedTextDecoder.decodeDisplayFields(JSON.parse(text));
            } catch (Exception e) {
                return new ArrayList<Object>();
            }
        }
        return EscapedTextDecoder.decodeDisplayFields(raw);
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
        if ("step8_img_analysis".equals(stepKey)) {
            // 图片流 Agent 分析：展示头像/背景/配图及 vision/OCR，不再读误回填的 report_analysis
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
        if ("step5_validated".equals(stepKey) || "step6_validated".equals(stepKey)) {
            return "collect_validated_accounts";
        }
        if (DEMO_STEP_KEYS.contains(stepKey)) {
            return "collect_step_demo_records";
        }
        if (stepKey.startsWith("step3_post_") || "step6_posts".equals(stepKey) || stepKey.startsWith("step6_post_")
                || "step7_posts".equals(stepKey) || stepKey.startsWith("step7_post_")) {
            return "collect_posts";
        }
        if ("step9_context_views".equals(stepKey)
                || "step10_context_pii".equals(stepKey)
                || "step11_report".equals(stepKey)) {
            return "report_analysis";
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

    /** 空的「发文作证：」槽位（后直接接下一观点或文末） */
    private static final Pattern EMPTY_CITATION_SLOT = Pattern.compile(
            "(发文作证\\s*[：:]\\s*\\n)(?=\\s*(?:###|\\Z))",
            Pattern.MULTILINE);

    /**
     * 观点分析缺作证时，从 collect_posts 补原文摘录（读时补救，兼容历史任务）。
     */
    private List<Map<String, Object>> enrichStep9PostEvidence(String taskId,
                                                             List<Map<String, Object>> records) {
        if (records == null || records.isEmpty()) {
            return records;
        }
        String content = extractReportAnalysisContent(records);
        if (content.isEmpty() || hasPostEvidence(content)) {
            return records;
        }
        List<Map<String, Object>> posts = collectTaskMapper.selectPostsByTaskId(taskId);
        if (posts == null || posts.isEmpty()) {
            return records;
        }
        String enriched = fillStep9PostEvidence(content, posts);
        if (enriched.equals(content)) {
            return records;
        }
        return replaceReportAnalysisContent(records, enriched);
    }

    private String extractReportAnalysisContent(List<Map<String, Object>> records) {
        for (Map<String, Object> rec : records) {
            Object fieldsObj = rec.get("fields");
            if (!(fieldsObj instanceof List)) {
                continue;
            }
            @SuppressWarnings("unchecked")
            List<Object> fields = (List<Object>) fieldsObj;
            for (Object fo : fields) {
                if (!(fo instanceof Map)) {
                    continue;
                }
                @SuppressWarnings("unchecked")
                Map<String, Object> f = (Map<String, Object>) fo;
                String label = stringVal(f.get("label"));
                if ("分析正文".equals(label) || "content".equals(label)) {
                    return stringVal(f.get("value"));
                }
            }
        }
        return "";
    }

    private List<Map<String, Object>> replaceReportAnalysisContent(List<Map<String, Object>> records,
                                                                    String newContent) {
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        boolean replaced = false;
        for (Map<String, Object> rec : records) {
            Map<String, Object> copy = new LinkedHashMap<String, Object>(rec);
            if (!replaced) {
                Object fieldsObj = copy.get("fields");
                if (fieldsObj instanceof List) {
                    @SuppressWarnings("unchecked")
                    List<Object> fields = (List<Object>) fieldsObj;
                    List<Map<String, Object>> newFields = new ArrayList<Map<String, Object>>();
                    boolean fieldDone = false;
                    for (Object fo : fields) {
                        if (!(fo instanceof Map)) {
                            continue;
                        }
                        @SuppressWarnings("unchecked")
                        Map<String, Object> f = (Map<String, Object>) fo;
                        Map<String, Object> nf = new LinkedHashMap<String, Object>(f);
                        String label = stringVal(f.get("label"));
                        if (!fieldDone && ("分析正文".equals(label) || "content".equals(label))) {
                            nf.put("value", newContent);
                            fieldDone = true;
                        }
                        newFields.add(nf);
                    }
                    if (!fieldDone) {
                        Map<String, Object> nf = new LinkedHashMap<String, Object>();
                        nf.put("label", "分析正文");
                        nf.put("value", newContent);
                        newFields.add(nf);
                    }
                    copy.put("fields", newFields);
                    replaced = true;
                }
            }
            out.add(copy);
        }
        return out;
    }

    private boolean hasPostEvidence(String text) {
        String t = text == null ? "" : text.trim();
        if (t.length() < 80) {
            return false;
        }
        if (t.contains("发文称「") || t.contains("发文称\"")) {
            return true;
        }
        if (Pattern.compile("\\d{4}\\s*年\\s*\\d{1,2}\\s*月\\s*\\d{1,2}\\s*日").matcher(t).find()) {
            return true;
        }
        if (Pattern.compile("发文作证\\s*[：:]\\s*\\n\\s*\\d+\\.").matcher(t).find()) {
            return true;
        }
        return false;
    }

    private String fillStep9PostEvidence(String content, List<Map<String, Object>> posts) {
        List<String> citations = new ArrayList<String>();
        int seq = 1;
        for (Map<String, Object> row : posts) {
            if (citations.size() >= 15) {
                break;
            }
            String line = formatPostCitation(seq, row);
            if (!line.isEmpty()) {
                citations.add(line);
                seq++;
            }
        }
        if (citations.isEmpty()) {
            return content;
        }
        Matcher m = EMPTY_CITATION_SLOT.matcher(content);
        if (m.find()) {
            StringBuffer sb = new StringBuffer();
            int postIdx = 0;
            m.reset();
            while (m.find()) {
                StringBuilder chunk = new StringBuilder();
                for (int k = 0; k < Math.min(3, citations.size()); k++) {
                    if (chunk.length() > 0) {
                        chunk.append("\n");
                    }
                    chunk.append(citations.get(postIdx % citations.size()));
                    postIdx++;
                }
                chunk.append("\n");
                m.appendReplacement(sb, Matcher.quoteReplacement(m.group(1) + chunk));
            }
            m.appendTail(sb);
            return sb.toString();
        }
        StringBuilder tail = new StringBuilder(content);
        tail.append("\n\n发文作证（库内入库原文摘录）：\n");
        for (int i = 0; i < Math.min(10, citations.size()); i++) {
            tail.append(citations.get(i)).append("\n");
        }
        return tail.toString().trim();
    }

    private String formatPostCitation(int seq, Map<String, Object> row) {
        String plat = stringVal(row.get("platform"));
        String excerpt = firstNonEmpty(
                stringVal(row.get("content_text")),
                stringVal(row.get("title")));
        excerpt = excerpt.replace("\n", " ").trim();
        if (excerpt.length() > 200) {
            excerpt = excerpt.substring(0, 200) + "…";
        }
        if (excerpt.isEmpty()) {
            return "";
        }
        String dateS = formatPublishedAt(row.get("published_at"));
        if (!dateS.isEmpty()) {
            return seq + ". " + dateS + "在" + plat + "平台发文称「" + excerpt + "」";
        }
        return seq + ". 在" + plat + "平台发文称「" + excerpt + "」";
    }

    private String formatPublishedAt(Object pub) {
        if (pub == null) {
            return "";
        }
        try {
            if (pub instanceof Timestamp) {
                return new SimpleDateFormat("yyyy年MM月dd日").format((Timestamp) pub);
            }
            if (pub instanceof Date) {
                return new SimpleDateFormat("yyyy年MM月dd日").format((Date) pub);
            }
            String s = String.valueOf(pub).trim();
            if (s.length() >= 10 && s.charAt(4) == '-' && s.charAt(7) == '-') {
                return s.substring(0, 4) + "年" + s.substring(5, 7) + "月" + s.substring(8, 10) + "日";
            }
        } catch (Exception ignored) {
            // fall through
        }
        return "";
    }
}
