package com.example.aw.collect.service;

import com.alibaba.fastjson.JSON;
import com.example.aw.collect.mapper.CollectImageMapper;
import com.example.aw.collect.mapper.CollectTaskMapper;
import com.example.aw.collect.registry.TaskTypeRegistry;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * 历史问答查询服务（按「任务」维度聚合数据）。
 * <p>
 * <b>与 /api/dialogues 的区别（务必读懂）：</b>
 * <ul>
 *   <li>dialogues：面向 hermes_user_dialogues 消息折叠，偏「聊天气泡」时间线；</li>
 *   <li>本服务：面向 hermes_tasks，一条任务 = 一次业务执行；详情聚合业务表数据。</li>
 * </ul>
 * <p>
 * <b>数据来源总览：</b>
 * <ul>
 *   <li>任务主表 —— hermes_tasks（状态、类型、时间）</li>
 *   <li>用户提问 —— hermes_user_dialogues（msg_type=user_input，含 payload_json）</li>
 *   <li>终稿报告 —— hermes_user_dialogues（msg_type=summary 优先）via {@link TaskFinalAnswerQueryService}</li>
 *   <li>账号 —— collect_profiles</li>
 *   <li>发文 —— collect_posts</li>
 *   <li>图片 —— collect_images，映射复用 {@link ImageAssetQueryService}</li>
 * </ul>
 * <p>
 * <b>字段命名约定：</b>库内多为下划线（account_id），对外 API 统一驼峰（accountId），
 * 中文展示值与英文 Code 成对返回（如 status / statusLabel、taskType / taskTypeCode）。
 */
@Service
public class HistoryQaQueryService {

    /**
     * 列表里「终稿预览」最大字符数。
     * 超过则截断并加省略号，避免列表接口把整篇报告拖回来。
     */
    private static final int PREVIEW_LEN = 200;

    /**
     * 详情接口一次拉取图片的条数上限。
     * ImageAssetQueryService.listTaskImages 单页 pageSize 上限是 100，
     * 这里用 500 并走其内部限制：实际每页最多 100 条，若图片更多需前端另调分页图片接口。
     * （当前实现只拉第 1 页，见 getHistoryTaskDetail 内注释。）
     */
    private static final int DETAIL_IMAGE_PAGE_SIZE = 500;

    /** 任务 / 对话 / 账号 / 发文 等综合 Mapper */
    @Autowired
    private CollectTaskMapper collectTaskMapper;

    /** 图片资产表 collect_images 的计数等 */
    @Autowired
    private CollectImageMapper collectImageMapper;

    /**
     * 复用已有图片列表映射（含 sourceType/storageStatus 中文、imageUrl 等），
     * 避免历史详情再维护一套字段转换。
     */
    @Autowired
    private ImageAssetQueryService imageAssetQueryService;

    /**
     * 复用终稿查询：优先 summary，否则 assistant_reply；都没有则 ready=false。
     */
    @Autowired
    private TaskFinalAnswerQueryService taskFinalAnswerQueryService;

    /**
     * 前端短码 ↔ 库内 task_type ↔ 中文业务名 对照表。
     * 例如 collect → account_collect → 「账号信息采集」。
     */
    @Autowired
    private TaskTypeRegistry taskTypeRegistry;

    /**
     * 历史任务分页列表。
     * <p>
     * <b>处理步骤：</b>
     * <ol>
     *   <li>规范化 page / pageSize（防前端传 0、负数、过大值）</li>
     *   <li>若传了 taskType，用 TaskTypeRegistry 转成库内 task_type；无法识别则 400</li>
     *   <li>若传了 status，只允许 pending/running/completed；否则 400</li>
     *   <li>先 count 再 SELECT 分页行（含 question / payload / answer_preview 子查询）</li>
     *   <li>把每行下划线字段转成前端驼峰列表项</li>
     * </ol>
     *
     * @param taskType 前端短码或库内类型，可空表示不限业务
     * @param status   可选单一状态过滤：pending / running / completed；空表示三种都要
     * @param page     页码（从 1 起）
     * @param pageSize 每页条数
     * @return 含 page、pageSize、total、list 的 Map；若带了筛选条件还会带顶层 taskType/status 中文信息
     */
    public Map<String, Object> listHistoryTasks(String taskType, String status, int page, int pageSize) {
        // ---------- 1. 分页参数纠偏 ----------
        if (page < 1) {
            page = 1;
        }
        if (pageSize < 1) {
            pageSize = 20;
        }
        // 上限 200：防止一次把全库任务拖垮内存
        if (pageSize > 200) {
            pageSize = 200;
        }

        // ---------- 2. 业务类型筛选：短码 → 库字段 ----------
        String dbTaskType = null;
        String filterLabel = null;
        if (taskType != null && !taskType.trim().isEmpty()) {
            // resolveExact：不认识就返回 null（不会静默回退成 collect）
            TaskTypeRegistry.TaskTypeDef def = taskTypeRegistry.resolveExact(taskType);
            if (def == null) {
                throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid_taskType");
            }
            dbTaskType = def.getDbTaskType();
            filterLabel = def.getLabel();
        }

        // ---------- 3. 状态筛选规范化 ----------
        String statusFilter = normalizeHistoryStatus(status);

        // ---------- 4. 查总数 + 当前页 ----------
        long total = collectTaskMapper.countHistoryTasks(dbTaskType, statusFilter);
        int offset = (page - 1) * pageSize;
        List<Map<String, Object>> rows = collectTaskMapper.selectHistoryTasksPage(
                dbTaskType, statusFilter, offset, pageSize);

        // ---------- 5. 行 → 列表项（驼峰 + 中文标签） ----------
        List<Map<String, Object>> list = new ArrayList<Map<String, Object>>();
        for (Map<String, Object> row : rows) {
            list.add(toListItem(row));
        }

        // ---------- 6. 组装分页外壳 ----------
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("page", page);
        body.put("pageSize", pageSize);
        body.put("total", Long.valueOf(total));
        // 仅在「调用方显式传了筛选」时回显，方便前端 Tab 确认当前过滤条件
        if (filterLabel != null) {
            body.put("taskType", filterLabel);
        }
        if (statusFilter != null) {
            body.put("status", statusFilter);
            body.put("statusLabel", labelStatus(statusFilter));
        }
        body.put("list", list);
        return body;
    }

    /**
     * 历史任务详情：一次返回本次任务的提问、报告、账号、发文、图片。
     * <p>
     * <b>处理步骤：</b>
     * <ol>
     *   <li>校验 taskId；查 hermes_tasks，不存在 → 404</li>
     *   <li>状态必须是历史可见（pending/running/completed），否则 → 409</li>
     *   <li>组装 question（user_input + payload_json）</li>
     *   <li>组装 report（复用 TaskFinalAnswerQueryService）</li>
     *   <li>拉取并映射 profiles / posts</li>
     *   <li>拉取 images（复用 ImageAssetQueryService，带中文状态与 imageUrl）</li>
     *   <li>附 counts 统计，方便详情页头展示数量徽章</li>
     * </ol>
     *
     * @param taskId 任务 ID
     * @return 详情 Map（字段见方法末尾 put 列表）
     */
    public Map<String, Object> getHistoryTaskDetail(String taskId) {
        // ---------- 1. 参数与任务存在性 ----------
        if (taskId == null || taskId.trim().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "taskId_required");
        }
        Map<String, Object> task = collectTaskMapper.selectTaskById(taskId.trim());
        if (task == null || task.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "task_not_found");
        }

        // ---------- 2. 状态白名单（与列表 SQL 保持一致） ----------
        String status = str(task.get("status"));
        if (!isHistoryVisibleStatus(status)) {
            // 例如 failed：不算「历史问答回顾」范围；前端可提示去别的入口看失败单
            throw new ResponseStatusException(HttpStatus.CONFLICT, "task_status_not_in_history");
        }

        String dbType = str(task.get("task_type"));

        // ---------- 3. 提问 + 终稿 ----------
        Map<String, Object> question = buildQuestion(taskId.trim());
        // report 结构：taskId/type/msgType/content/createdAt/ready
        Map<String, Object> report = taskFinalAnswerQueryService.getFinalAnswer(taskId.trim());

        // ---------- 4. 账号列表（collect_profiles → 驼峰） ----------
        List<Map<String, Object>> profileRows = collectTaskMapper.selectProfilesByTaskId(taskId.trim());
        List<Map<String, Object>> profiles = new ArrayList<Map<String, Object>>();
        if (profileRows != null) {
            for (Map<String, Object> row : profileRows) {
                profiles.add(toProfileItem(row));
            }
        }

        // ---------- 5. 发文列表（collect_posts → 驼峰，含 media） ----------
        List<Map<String, Object>> postRows = collectTaskMapper.selectPostsByTaskId(taskId.trim());
        List<Map<String, Object>> posts = new ArrayList<Map<String, Object>>();
        if (postRows != null) {
            for (Map<String, Object> row : postRows) {
                posts.add(toPostItem(row));
            }
        }

        // ---------- 6. 图片列表 ----------
        // 复用 ImageAssetQueryService：返回项已含 sourceType、storageStatus、analyzeStatus
        // 中文与 *Code，以及 imageUrl=/api/images/{id}/bytes。
        // 注意：listTaskImages 内部 pageSize 上限是 100，这里传 500 会被截成 100。
        // 若单任务图片超过 100，详情里 images 只含前 100 条；总量看 counts.images，
        // 完整分页请调 GET /api/tasks/{taskId}/images。
        Map<String, Object> imagePage = imageAssetQueryService.listTaskImages(
                taskId.trim(), null, null, null, 1, DETAIL_IMAGE_PAGE_SIZE);
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> images = imagePage.get("list") instanceof List
                ? (List<Map<String, Object>>) imagePage.get("list")
                : new ArrayList<Map<String, Object>>();

        // ---------- 7. 数量统计（与数组长度可能不一致：图片超过单页上限时） ----------
        long profileCount = collectTaskMapper.countProfilesByTaskId(taskId.trim());
        long postCount = collectTaskMapper.countPostsByTaskId(taskId.trim());
        long imageCount = collectImageMapper.countByTask(taskId.trim(), null, null, null);

        Map<String, Object> counts = new LinkedHashMap<String, Object>();
        counts.put("profiles", Long.valueOf(profileCount));
        counts.put("posts", Long.valueOf(postCount));
        counts.put("images", Long.valueOf(imageCount));

        // ---------- 8. 组装详情响应 ----------
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("taskId", taskId.trim());
        body.put("sessionId", task.get("session_id"));
        // 对外中文业务名 + 短码，前端 Tab / 路由都好用
        body.put("taskType", taskTypeRegistry.labelOfDbTaskType(dbType));
        body.put("taskTypeCode", taskTypeRegistry.frontendCodeOfDbTaskType(dbType));
        body.put("status", status);
        body.put("statusLabel", labelStatus(status));
        body.put("createdAt", task.get("created_at"));
        body.put("startedAt", task.get("started_at"));
        body.put("finishedAt", task.get("finished_at"));
        body.put("question", question);
        body.put("report", report);
        body.put("counts", counts);
        body.put("profiles", profiles);
        body.put("posts", posts);
        body.put("images", images);
        return body;
    }

    // ========================= 列表项 / 明细字段映射 =========================

    /**
     * 把 SQL 查出来的一行历史任务，转成列表接口的一条驼峰 JSON。
     * <p>
     * SQL 侧已通过子查询带上 question、payload_json、answer_preview，
     * 这里不做二次查库，保证列表接口轻量。
     */
    private Map<String, Object> toListItem(Map<String, Object> row) {
        String taskId = str(row.get("task_id"));
        String dbType = str(row.get("task_type"));
        String status = str(row.get("status"));
        String question = str(row.get("question"));
        // answer_preview 来自 summary.content；没有终稿则为空串
        String preview = str(row.get("answer_preview"));
        boolean hasAnswer = preview.length() > 0;

        Map<String, Object> item = new LinkedHashMap<String, Object>();
        item.put("taskId", taskId);
        item.put("sessionId", row.get("session_id"));
        item.put("taskType", taskTypeRegistry.labelOfDbTaskType(dbType));
        item.put("taskTypeCode", taskTypeRegistry.frontendCodeOfDbTaskType(dbType));
        item.put("status", status);
        item.put("statusLabel", labelStatus(status));
        // 用户原始提问文案（可能带 skill 前缀，如 account-intelligence-collect ...）
        item.put("question", question);
        // 前端建任务时传入的表单 JSON；没有则为 null（与 dialogues 历史回填语义一致）
        item.put("payload", parseJson(row.get("payload_json")));
        item.put("hasAnswer", Boolean.valueOf(hasAnswer));
        // 列表只给预览，完整报告去详情的 report 字段
        item.put("answerPreview", hasAnswer ? clip(preview, PREVIEW_LEN) : null);
        item.put("createdAt", row.get("created_at"));
        item.put("startedAt", row.get("started_at"));
        item.put("finishedAt", row.get("finished_at"));
        // 方便前端直接拼详情请求，不必自己拼路径
        item.put("detailUrl", "/api/history/tasks/" + taskId);
        return item;
    }

    /**
     * 组装详情里的 question 节点。
     * <p>
     * 数据源：hermes_user_dialogues 中该任务最早一条 msg_type=user_input。
     * payload_json 可能是 JSON 对象字符串，解析失败则退回原始字符串。
     */
    private Map<String, Object> buildQuestion(String taskId) {
        Map<String, Object> row = collectTaskMapper.selectUserInput(taskId);
        Map<String, Object> q = new LinkedHashMap<String, Object>();
        if (row == null || row.isEmpty()) {
            // 任务预建异常、或极老数据没有 user_input 时，仍返回结构完整的空对象，前端好判空
            q.put("content", null);
            q.put("payload", null);
            q.put("createdAt", null);
            return q;
        }
        q.put("content", row.get("content"));
        q.put("payload", parseJson(row.get("payload_json")));
        q.put("createdAt", row.get("created_at"));
        return q;
    }

    /**
     * collect_profiles 一行 → 前端账号卡片字段。
     * 不做平台中文化，保留库内 platform 原值（twitter/instagram/...），与其它业务接口一致。
     */
    private Map<String, Object> toProfileItem(Map<String, Object> row) {
        Map<String, Object> item = new LinkedHashMap<String, Object>();
        item.put("id", row.get("id"));
        item.put("platform", row.get("platform"));
        item.put("accountId", row.get("account_id"));
        item.put("accountHandle", row.get("account_handle"));
        item.put("displayName", row.get("display_name"));
        item.put("bio", row.get("bio"));
        // CDN 头像地址，可能过期；稳定查看请走图片资产入库后的 bytes 接口
        item.put("avatarUrl", row.get("avatar_url"));
        item.put("profileUrl", row.get("profile_url"));
        item.put("followerCount", row.get("follower_count"));
        item.put("followingCount", row.get("following_count"));
        item.put("contentCount", row.get("content_count"));
        item.put("verified", row.get("verified"));
        item.put("visibility", row.get("visibility"));
        item.put("collectStatus", row.get("collect_status"));
        item.put("collectedAt", row.get("collected_at"));
        return item;
    }

    /**
     * collect_posts 一行 → 前端发文卡片字段。
     * media_json 解析为 media 数组（[{type,url,thumb}, ...]），解析失败则原样字符串。
     */
    private Map<String, Object> toPostItem(Map<String, Object> row) {
        Map<String, Object> item = new LinkedHashMap<String, Object>();
        item.put("id", row.get("id"));
        item.put("platform", row.get("platform"));
        item.put("accountId", row.get("account_id"));
        // 平台内内容唯一 ID（推文 ID / 视频 ID 等）
        item.put("contentId", row.get("content_id"));
        item.put("contentType", row.get("content_type"));
        item.put("title", row.get("title"));
        item.put("contentText", row.get("content_text"));
        item.put("contentUrl", row.get("content_url"));
        item.put("publishedAt", row.get("published_at"));
        item.put("viewCount", row.get("view_count"));
        item.put("likeCount", row.get("like_count"));
        item.put("commentCount", row.get("comment_count"));
        item.put("repostCount", row.get("repost_count"));
        item.put("media", parseJson(row.get("media_json")));
        item.put("createdAt", row.get("created_at"));
        return item;
    }

    // ========================= 状态 / 工具方法 =========================

    /**
     * 规范化列表接口传入的 status 参数。
     * <ul>
     *   <li>空 / 空白 → null，表示不额外按单状态过滤（SQL 仍限制在三种可见状态内）</li>
     *   <li>pending / running / completed → 原样小写返回</li>
     *   <li>其它值 → 400 invalid_status</li>
     * </ul>
     */
    private static String normalizeHistoryStatus(String status) {
        if (status == null || status.trim().isEmpty()) {
            return null;
        }
        String s = status.trim().toLowerCase(Locale.ROOT);
        if ("pending".equals(s) || "running".equals(s) || "completed".equals(s)) {
            return s;
        }
        throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "invalid_status");
    }

    /**
     * 判断任务状态是否允许出现在历史问答接口中。
     * 当前与列表 SQL 的 IN ('pending','running','completed') 保持一致。
     */
    /**
     * 按 taskId 删除该任务在 MySQL 中的全部关联数据（含 hermes_tasks 自身）。
     * <p>
     * <b>删除范围（当前库含 task_id 的表）：</b>
     * collect_images、collect_display_records、collect_identity_streams、
     * collect_phase_steps、collect_posts、collect_profiles、collect_task_summaries、
     * collect_validated_accounts、cross_platform_candidates、hermes_tool_outputs、
     * hermes_user_dialogues，最后 hermes_tasks。
     * <p>
     * <b>注意：</b>HBase 中图片二进制（collect_image_bytes）不会随本接口删除。
     * <p>
     * 同一事务内执行，任一 DELETE 失败则全部回滚。
     *
     * @param taskId 任务 ID
     * @return ok=true、taskId、deleted（各表实际删除行数）
     */
    @Transactional(rollbackFor = Exception.class)
    public Map<String, Object> deleteHistoryTask(String taskId) {
        if (taskId == null || taskId.trim().isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "taskId_required");
        }
        String tid = taskId.trim();
        Map<String, Object> task = collectTaskMapper.selectTaskById(tid);
        if (task == null || task.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "task_not_found");
        }

        // 先删有 FK 的子表 collect_images，再删其余业务表，最后删主表
        Map<String, Object> deleted = new LinkedHashMap<String, Object>();
        deleted.put("images", Integer.valueOf(collectTaskMapper.deleteCollectImagesByTaskId(tid)));
        deleted.put("displayRecords", Integer.valueOf(collectTaskMapper.deleteCollectDisplayRecordsByTaskId(tid)));
        deleted.put("identityStreams", Integer.valueOf(collectTaskMapper.deleteCollectIdentityStreamsByTaskId(tid)));
        deleted.put("phaseSteps", Integer.valueOf(collectTaskMapper.deleteCollectPhaseStepsByTaskId(tid)));
        deleted.put("posts", Integer.valueOf(collectTaskMapper.deleteCollectPostsByTaskId(tid)));
        deleted.put("profiles", Integer.valueOf(collectTaskMapper.deleteCollectProfilesByTaskId(tid)));
        deleted.put("taskSummaries", Integer.valueOf(collectTaskMapper.deleteCollectTaskSummariesByTaskId(tid)));
        deleted.put("validatedAccounts", Integer.valueOf(collectTaskMapper.deleteCollectValidatedAccountsByTaskId(tid)));
        deleted.put("crossPlatformCandidates", Integer.valueOf(collectTaskMapper.deleteCrossPlatformCandidatesByTaskId(tid)));
        deleted.put("toolOutputs", Integer.valueOf(collectTaskMapper.deleteHermesToolOutputsByTaskId(tid)));
        deleted.put("dialogues", Integer.valueOf(collectTaskMapper.deleteHermesUserDialoguesByTaskId(tid)));
        deleted.put("task", Integer.valueOf(collectTaskMapper.deleteHermesTaskById(tid)));

        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("ok", Boolean.TRUE);
        body.put("taskId", tid);
        body.put("deleted", deleted);
        return body;
    }

    private static boolean isHistoryVisibleStatus(String status) {
        return "pending".equals(status) || "running".equals(status) || "completed".equals(status);
    }

    /**
     * 任务状态英文码 → 前端中文文案。
     * 未知状态原样返回，便于以后扩展新状态时不悄悄吞掉信息。
     */
    private static String labelStatus(String status) {
        if ("pending".equals(status)) {
            return "待开始";
        }
        if ("running".equals(status)) {
            return "进行中";
        }
        if ("completed".equals(status)) {
            return "已完成";
        }
        if ("failed".equals(status)) {
            return "失败";
        }
        return status;
    }

    /**
     * 截断过长文本，用于列表 answerPreview。
     * 截断后追加中文省略号「…」。
     */
    private static String clip(String text, int max) {
        if (text == null) {
            return null;
        }
        String t = text.trim();
        if (t.length() <= max) {
            return t;
        }
        return t.substring(0, max) + "…";
    }

    /**
     * 把 MySQL JSON 列 / JSON 字符串解析成 Java 对象（Map/List），供 Fastjson 再序列化给前端。
     * <p>
     * 兼容几种库驱动行为：
     * <ul>
     *   <li>已经是 Map/List —— 直接返回</li>
     *   <li>字符串 "{...}" / "[...]" —— JSON.parse</li>
     *   <li>空串 / "null" —— 返回 null</li>
     *   <li>解析失败 —— 返回原始字符串，避免整条接口 500</li>
     * </ul>
     */
    private static Object parseJson(Object raw) {
        if (raw == null) {
            return null;
        }
        if (raw instanceof Map || raw instanceof List) {
            return raw;
        }
        String text = String.valueOf(raw).trim();
        if (text.isEmpty() || "null".equalsIgnoreCase(text)) {
            return null;
        }
        try {
            return JSON.parse(text);
        } catch (Exception e) {
            return text;
        }
    }

    /**
     * null 安全转字符串并 trim；null → 空串。
     * 用于 status/taskType 等后续要做 equals 判断的字段。
     */
    private static String str(Object v) {
        return v == null ? "" : String.valueOf(v).trim();
    }
}
