package com.example.aw.collect.service;

import com.example.aw.collect.mapper.CollectVideoMapper;
import com.example.aw.hbase.HBaseImageClient;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ResponseStatusException;

import java.io.File;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 历史详情视频资产：mp4 静态 URL + 抽帧 HBase dataUrl + 分析正文。
 */
@Service
public class VideoAssetQueryService {

    private static final Logger log = LoggerFactory.getLogger(VideoAssetQueryService.class);

    @Autowired
    private CollectVideoMapper collectVideoMapper;

    @Autowired
    private HBaseImageClient hBaseImageClient;

    @Value("${hermes.video.public-base-url:http://192.168.100.39:4377}")
    private String publicBaseUrl;

    @Value("${hermes.video.local-dir:D:/hermes-xa/data/video_bytes}")
    private String videoLocalDir;

    @Value("${hermes.video.url-prefix:/video-files}")
    private String videoUrlPrefix;

    /**
     * 历史详情：任务下全部视频 + 帧（有 hbase_row_key 则尽力填 dataUrl；老帧无 key 不回填）。
     */
    public List<Map<String, Object>> listTaskVideosWithFrames(String taskId, int maxBytesPerFrame) {
        if (taskId == null || taskId.trim().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "taskId_required");
        }
        if (maxBytesPerFrame < 64 * 1024) {
            maxBytesPerFrame = 64 * 1024;
        }
        String tid = taskId.trim();
        List<Map<String, Object>> videos = collectVideoMapper.selectVideosByTask(tid);
        List<Map<String, Object>> out = new ArrayList<Map<String, Object>>();
        if (videos == null) {
            return out;
        }
        for (Map<String, Object> v : videos) {
            out.add(toVideoItem(v, maxBytesPerFrame));
        }
        return out;
    }

    public long countVideosByTask(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            return 0L;
        }
        return collectVideoMapper.countVideosByTask(taskId.trim());
    }

    private Map<String, Object> toVideoItem(Map<String, Object> row, int maxBytesPerFrame) {
        Map<String, Object> item = new LinkedHashMap<String, Object>();
        String videoId = str(row.get("video_id"));
        item.put("videoId", videoId);
        item.put("platform", row.get("platform"));
        item.put("postId", row.get("post_id"));
        item.put("accountId", row.get("account_id"));
        item.put("originUrl", row.get("origin_url"));
        item.put("storageStatus", row.get("storage_status"));
        item.put("analyzeStatus", row.get("analyze_status"));
        item.put("durationSec", row.get("duration_sec"));
        item.put("width", row.get("width"));
        item.put("height", row.get("height"));
        item.put("frameExtractedCnt", row.get("frame_extracted_cnt"));
        item.put("frameAnalyzedCnt", row.get("frame_analyzed_cnt"));
        item.put("frameStoredCnt", row.get("frame_stored_cnt"));
        item.put("videoAnalysisText", row.get("video_analysis_text"));
        item.put("errorMessage", row.get("error_message"));
        item.put("videoUrl", buildVideoUrl(str(row.get("local_path")), str(row.get("task_id")), videoId));

        List<Map<String, Object>> frames = new ArrayList<Map<String, Object>>();
        List<Map<String, Object>> frameRows = collectVideoMapper.selectFramesByVideoId(videoId);
        if (frameRows != null) {
            for (Map<String, Object> fr : frameRows) {
                frames.add(toFrameItem(fr, maxBytesPerFrame));
            }
        }
        item.put("frames", frames);
        return item;
    }

    private Map<String, Object> toFrameItem(Map<String, Object> row, int maxBytesPerFrame) {
        Map<String, Object> item = new LinkedHashMap<String, Object>();
        item.put("frameId", row.get("frame_id"));
        item.put("frameIndex", row.get("frame_index"));
        item.put("timestampSec", row.get("timestamp_sec"));
        Object preview = row.get("is_preview");
        item.put("isPreview", preview != null && !"0".equals(String.valueOf(preview))
                && !Boolean.FALSE.equals(preview));
        item.put("visionText", row.get("vision_text"));
        item.put("analyzeStatus", row.get("analyze_status"));
        item.put("storageStatus", row.get("storage_status"));
        item.put("mimeType", row.get("mime_type"));
        item.put("hasStoredBytes", Boolean.FALSE);
        item.put("dataUrl", null);
        attachFrameDataUrl(item, row, maxBytesPerFrame);
        return item;
    }

    private void attachFrameDataUrl(Map<String, Object> item, Map<String, Object> row, int maxBytesPerFrame) {
        String storageStatus = str(row.get("storage_status"));
        String rowKey = str(row.get("hbase_row_key"));
        String frameId = str(row.get("frame_id"));
        // 老任务无 hbase_row_key：不读本地、不回填
        if (!"stored".equals(storageStatus) || rowKey.isEmpty()) {
            return;
        }
        try {
            Map<String, Object> blob = hBaseImageClient.getImageBytes(rowKey);
            if (blob == null || blob.get("bytes") == null) {
                item.put("storeReadError", "bytes_not_found");
                return;
            }
            byte[] bytes = (byte[]) blob.get("bytes");
            if (bytes.length == 0) {
                item.put("storeReadError", "empty_bytes");
                return;
            }
            item.put("hasStoredBytes", Boolean.TRUE);
            item.put("storedBytes", Integer.valueOf(bytes.length));
            if (bytes.length > maxBytesPerFrame) {
                return;
            }
            String mime = str(blob.get("mimeType"));
            if (mime.isEmpty()) {
                mime = str(row.get("mime_type"));
            }
            if (mime.isEmpty()) {
                mime = "image/jpeg";
            }
            item.put("mimeType", mime);
            item.put("dataUrl", "data:" + mime + ";base64," + Base64.getEncoder().encodeToString(bytes));
        } catch (Exception e) {
            log.warn("[历史视频帧] 读存储跳过 frameId={} rowKey={} err={}", frameId, rowKey, e.getMessage());
            item.put("storeReadError", e.getMessage() == null ? "hbase_read_failed" : e.getMessage());
        }
    }

    /**
     * 将 local_path 转为可播完整 URL；文件不存在则 null。
     */
    String buildVideoUrl(String localPath, String taskId, String videoId) {
        String relative = resolveRelativeVideoPath(localPath, taskId, videoId);
        if (relative == null || relative.isEmpty()) {
            return null;
        }
        File f = new File(normalizeLocalDir(), relative.replace("/", File.separator));
        if (!f.isFile()) {
            return null;
        }
        String base = trimTrailingSlash(publicBaseUrl == null ? "" : publicBaseUrl.trim());
        String prefix = normalizeUrlPrefix(videoUrlPrefix);
        return base + prefix + "/" + relative.replace("\\", "/");
    }

    private String resolveRelativeVideoPath(String localPath, String taskId, String videoId) {
        if (localPath != null && !localPath.trim().isEmpty()) {
            String abs = localPath.trim().replace("\\", "/");
            String root = normalizeLocalDir().replace("\\", "/");
            if (!root.endsWith("/")) {
                root = root + "/";
            }
            String absLower = abs.toLowerCase();
            String rootLower = root.toLowerCase();
            if (absLower.startsWith(rootLower)) {
                return abs.substring(root.length());
            }
            // 路径不在配置根下时，仍尝试 taskId/videoId/source.mp4
        }
        if (taskId != null && !taskId.isEmpty() && videoId != null && !videoId.isEmpty()) {
            String candidate = taskId + "/" + videoId + "/source.mp4";
            File f = new File(normalizeLocalDir(), candidate.replace("/", File.separator));
            if (f.isFile()) {
                return candidate;
            }
        }
        return null;
    }

    private String normalizeLocalDir() {
        String dir = videoLocalDir == null ? "D:/hermes-xa/data/video_bytes" : videoLocalDir.trim();
        return dir.replace("/", File.separator);
    }

    private static String normalizeUrlPrefix(String prefix) {
        String p = prefix == null ? "/video-files" : prefix.trim();
        if (!p.startsWith("/")) {
            p = "/" + p;
        }
        if (p.endsWith("/")) {
            p = p.substring(0, p.length() - 1);
        }
        return p;
    }

    private static String trimTrailingSlash(String s) {
        if (s == null || s.isEmpty()) {
            return "";
        }
        while (s.endsWith("/")) {
            s = s.substring(0, s.length() - 1);
        }
        return s;
    }

    private static String str(Object o) {
        return o == null ? "" : String.valueOf(o).trim();
    }
}
