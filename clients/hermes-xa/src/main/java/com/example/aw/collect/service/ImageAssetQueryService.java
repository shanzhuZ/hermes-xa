package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.JSONObject;
import com.example.aw.collect.mapper.CollectImageMapper;
import com.example.aw.hbase.HBaseImageClient;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 图片资产查询：列表、详情、原图字节。
 * <p>
 * 数据来源：MySQL collect_images + HBase（或本地回退）原图。
 * 前端禁止直连 HBase，只通过本服务访问。
 */
@Service
public class ImageAssetQueryService {

    @Autowired
    private CollectImageMapper collectImageMapper;

    @Autowired
    private HBaseImageClient hBaseImageClient;

    /**
     * 分页查询任务下图片列表。
     *
     * @param taskId         任务 ID
     * @param sourceType     可选：profile_avatar / profile_cover / post_media
     * @param analyzeStatus  可选：pending / running / completed / failed / skipped
     * @param storageStatus  可选：pending / downloading / stored / failed
     * @param page           页码，从 1 起
     * @param pageSize       每页条数
     * @return 含 total / list 的分页结构；枚举展示中文，并附带 Code 字段
     */
    public Map<String, Object> listTaskImages(String taskId,
                                              String sourceType,
                                              String analyzeStatus,
                                              String storageStatus,
                                              int page,
                                              int pageSize) {
        if (taskId == null || taskId.trim().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "taskId_required");
        }
        if (page < 1) {
            page = 1;
        }
        if (pageSize < 1) {
            pageSize = 20;
        }
        if (pageSize > 100) {
            pageSize = 100;
        }
        int offset = (page - 1) * pageSize;
        long total = collectImageMapper.countByTask(taskId, sourceType, analyzeStatus, storageStatus);
        List<Map<String, Object>> rows = collectImageMapper.selectPageByTask(
                taskId, sourceType, analyzeStatus, storageStatus, offset, pageSize);

        List<Map<String, Object>> list = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> row : rows) {
            list.add(toListItem(row));
        }

        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("taskId", taskId);
        body.put("page", page);
        body.put("pageSize", pageSize);
        body.put("total", total);
        body.put("list", list);
        return body;
    }

    /**
     * 查询单张图片完整元数据与分析结果。
     *
     * @param imageId 图片 ID
     * @return 详情 Map；不存在抛 404
     */
    public Map<String, Object> getImageDetail(String imageId) {
        Map<String, Object> row = requireImage(imageId);
        return toDetail(row);
    }

    /**
     * 读取原图字节。
     * <p>
     * 步骤：查 MySQL → 校验 storage_status=stored 且 hbase_row_key 非空 → 读 HBase/本地。
     *
     * @param imageId 图片 ID
     * @return 含 bytes、mimeType、fileName；404/409/500 见 Controller
     */
    public Map<String, Object> getImageBytes(String imageId) {
        Map<String, Object> row = requireImage(imageId);
        String storageStatus = str(row.get("storage_status"));
        String rowKey = str(row.get("hbase_row_key"));
        if (!"stored".equals(storageStatus) || rowKey.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.CONFLICT, "image_not_stored");
        }
        try {
            Map<String, Object> blob = hBaseImageClient.getImageBytes(rowKey);
            if (blob == null || blob.get("bytes") == null) {
                throw new ResponseStatusException(HttpStatus.NOT_FOUND, "image_bytes_not_found");
            }
            String mime = str(blob.get("mimeType"));
            if (mime.isEmpty()) {
                mime = str(row.get("mime_type"));
            }
            if (mime.isEmpty()) {
                mime = "application/octet-stream";
            }
            Map<String, Object> out = new HashMap<String, Object>();
            out.put("bytes", blob.get("bytes"));
            out.put("mimeType", mime);
            out.put("fileName", imageId);
            return out;
        } catch (ResponseStatusException e) {
            throw e;
        } catch (Exception e) {
            throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR,
                    "hbase_read_failed: " + e.getMessage());
        }
    }

    private Map<String, Object> requireImage(String imageId) {
        if (imageId == null || imageId.trim().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "imageId_required");
        }
        Map<String, Object> row = collectImageMapper.selectByImageId(imageId.trim());
        if (row == null || row.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "image_not_found");
        }
        return row;
    }

    private Map<String, Object> toListItem(Map<String, Object> row) {
        String imageId = str(row.get("image_id"));
        String sourceCode = str(row.get("source_type"));
        String storageCode = str(row.get("storage_status"));
        String analyzeCode = str(row.get("analyze_status"));

        Map<String, Object> item = new LinkedHashMap<String, Object>();
        item.put("imageId", imageId);
        item.put("sourceType", labelSourceType(sourceCode));
        item.put("sourceTypeCode", sourceCode);
        item.put("platform", labelPlatform(str(row.get("platform"))));
        item.put("platformCode", str(row.get("platform")));
        item.put("accountId", row.get("account_id"));
        item.put("postId", row.get("post_id"));
        item.put("originUrl", row.get("origin_url"));
        item.put("storageStatus", labelStorageStatus(storageCode));
        item.put("storageStatusCode", storageCode);
        item.put("analyzeStatus", labelAnalyzeStatus(analyzeCode));
        item.put("analyzeStatusCode", analyzeCode);
        item.put("ocrText", row.get("ocr_text"));
        item.put("visionText", row.get("vision_text"));
        item.put("analysis", parseJson(row.get("analysis_json")));
        item.put("imageUrl", "/api/images/" + imageId + "/bytes");
        item.put("createdAt", row.get("created_at"));
        return item;
    }

    private Map<String, Object> toDetail(Map<String, Object> row) {
        Map<String, Object> detail = toListItem(row);
        detail.put("taskId", row.get("task_id"));
        detail.put("taskType", row.get("task_type"));
        detail.put("profileId", row.get("profile_id"));
        detail.put("mimeType", row.get("mime_type"));
        detail.put("fileSize", row.get("file_size"));
        detail.put("width", row.get("width"));
        detail.put("height", row.get("height"));
        detail.put("errorMessage", row.get("error_message"));
        detail.put("retryCount", row.get("retry_count"));
        detail.put("storedAt", row.get("stored_at"));
        detail.put("analyzedAt", row.get("analyzed_at"));
        detail.put("updatedAt", row.get("updated_at"));
        // 不向前端返回 hbase_row_key / content_sha256，避免暴露存储细节
        return detail;
    }

    private static Object parseJson(Object raw) {
        if (raw == null) {
            return null;
        }
        if (raw instanceof Map) {
            return raw;
        }
        String text = String.valueOf(raw).trim();
        if (text.isEmpty()) {
            return null;
        }
        try {
            return JSON.parse(text);
        } catch (Exception e) {
            JSONObject fallback = new JSONObject();
            fallback.put("raw", text);
            return fallback;
        }
    }

    private static String str(Object v) {
        return v == null ? "" : String.valueOf(v).trim();
    }

    private static String labelSourceType(String code) {
        if ("profile_avatar".equals(code)) {
            return "主页头像";
        }
        if ("profile_cover".equals(code)) {
            return "主页封面";
        }
        if ("post_media".equals(code)) {
            return "发文配图";
        }
        return code;
    }

    private static String labelStorageStatus(String code) {
        if ("pending".equals(code)) {
            return "待下载";
        }
        if ("downloading".equals(code)) {
            return "下载中";
        }
        if ("stored".equals(code)) {
            return "已存储";
        }
        if ("failed".equals(code)) {
            return "存储失败";
        }
        return code;
    }

    private static String labelAnalyzeStatus(String code) {
        if ("pending".equals(code)) {
            return "待分析";
        }
        if ("running".equals(code)) {
            return "分析中";
        }
        if ("completed".equals(code)) {
            return "分析完成";
        }
        if ("failed".equals(code)) {
            return "分析失败";
        }
        if ("skipped".equals(code)) {
            return "已跳过";
        }
        return code;
    }

    private static String labelPlatform(String code) {
        if (code == null || code.isEmpty()) {
            return code;
        }
        String lower = code.toLowerCase();
        if ("twitter".equals(lower) || "x".equals(lower)) {
            return "Twitter/X";
        }
        if ("instagram".equals(lower)) {
            return "Instagram";
        }
        if ("facebook".equals(lower)) {
            return "Facebook";
        }
        if ("youtube".equals(lower)) {
            return "YouTube";
        }
        if ("tiktok".equals(lower)) {
            return "TikTok";
        }
        if ("weibo".equals(lower)) {
            return "微博";
        }
        if ("telegram".equals(lower)) {
            return "Telegram";
        }
        if ("github".equals(lower)) {
            return "GitHub";
        }
        if ("bilibili".equals(lower)) {
            return "B站";
        }
        return code;
    }
}
