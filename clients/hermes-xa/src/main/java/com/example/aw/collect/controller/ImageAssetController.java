package com.example.aw.collect.controller;

import com.example.aw.collect.service.ImageAssetQueryService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * 图片资产 REST API：列表、详情、原图字节。
 * <p>
 * 前端只调本接口，禁止直连 HBase。
 */
@RestController
@RequestMapping("/api")
public class ImageAssetController {

    @Autowired
    private ImageAssetQueryService imageAssetQueryService;

    /**
     * 任务下图片分页列表。
     * <p>
     * 筛选参数使用英文枚举；响应中展示字段为中文，并附带 *Code。
     */
    @GetMapping("/tasks/{taskId}/images")
    public ResponseEntity<?> listTaskImages(
            @PathVariable("taskId") String taskId,
            @RequestParam(value = "sourceType", required = false) String sourceType,
            @RequestParam(value = "analyzeStatus", required = false) String analyzeStatus,
            @RequestParam(value = "storageStatus", required = false) String storageStatus,
            @RequestParam(value = "page", defaultValue = "1") int page,
            @RequestParam(value = "pageSize", defaultValue = "20") int pageSize) {
        try {
            Map<String, Object> body = imageAssetQueryService.listTaskImages(
                    taskId, sourceType, analyzeStatus, storageStatus, page, pageSize);
            return ResponseEntity.ok(body);
        } catch (ResponseStatusException e) {
            return error(e);
        } catch (Exception e) {
            return error(HttpStatus.INTERNAL_SERVER_ERROR, "list_images_failed", e.getMessage());
        }
    }

    /**
     * 单张图片详情（含 OCR / Vision / analysis）。
     */
    @GetMapping("/images/{imageId}")
    public ResponseEntity<?> getImageDetail(@PathVariable("imageId") String imageId) {
        try {
            return ResponseEntity.ok(imageAssetQueryService.getImageDetail(imageId));
        } catch (ResponseStatusException e) {
            return error(e);
        } catch (Exception e) {
            return error(HttpStatus.INTERNAL_SERVER_ERROR, "get_image_failed", e.getMessage());
        }
    }

    /**
     * 原图二进制。
     * <p>
     * 404：记录不存在或 HBase/本地无字节；409：尚未入库；500：存储读取异常。
     */
    @GetMapping("/images/{imageId}/bytes")
    public ResponseEntity<?> getImageBytes(@PathVariable("imageId") String imageId) {
        try {
            Map<String, Object> blob = imageAssetQueryService.getImageBytes(imageId);
            byte[] bytes = (byte[]) blob.get("bytes");
            String mimeType = String.valueOf(blob.get("mimeType"));
            MediaType mediaType;
            try {
                mediaType = MediaType.parseMediaType(mimeType);
            } catch (Exception e) {
                mediaType = MediaType.APPLICATION_OCTET_STREAM;
            }
            return ResponseEntity.ok()
                    .contentType(mediaType)
                    .contentLength(bytes.length)
                    .cacheControl(CacheControl.maxAge(7, TimeUnit.DAYS).cachePublic())
                    .header(HttpHeaders.CONTENT_DISPOSITION, "inline; filename=\"" + imageId + "\"")
                    .body(bytes);
        } catch (ResponseStatusException e) {
            return error(e);
        } catch (Exception e) {
            return error(HttpStatus.INTERNAL_SERVER_ERROR, "get_image_bytes_failed", e.getMessage());
        }
    }

    private ResponseEntity<Map<String, Object>> error(ResponseStatusException e) {
        HttpStatus status = e.getStatus();
        String reason = e.getReason() == null ? status.getReasonPhrase() : e.getReason();
        return error(status, reason, reason);
    }

    private ResponseEntity<Map<String, Object>> error(HttpStatus status, String error, String detail) {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("error", error);
        body.put("detail", detail);
        return ResponseEntity.status(status).body(body);
    }
}
