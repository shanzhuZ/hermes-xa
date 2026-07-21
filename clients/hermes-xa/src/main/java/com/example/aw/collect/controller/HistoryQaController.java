package com.example.aw.collect.controller;

import com.example.aw.collect.service.HistoryQaQueryService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 历史问答 REST 控制器（独立于 {@code CollectApiController} 的 /api/dialogues）。
 * <p>
 * <b>为什么单独建一套接口？</b>
 * <ul>
 *   <li>/api/dialogues 是按「对话消息」折叠展示，容易拿到 thoughts_final，且不强调任务状态；</li>
 *   <li>本控制器按「任务」维度：一条记录 = 一次采集/扩建/核查/写报任务；</li>
 *   <li>列表只关心进行中与已完成；详情一次给出账号/发文/图片/终稿，方便前端历史回顾页。</li>
 * </ul>
 * <p>
 * <b>对外路由前缀：</b>{@code /api/history}
 * <ul>
 *   <li>{@code GET /api/history/tasks} —— 历史任务列表</li>
 *   <li>{@code GET /api/history/tasks/{taskId}} —— 历史任务详情</li>
 *   <li>{@code DELETE /api/history/tasks/{taskId}} —— 删除任务及其 MySQL 关联数据</li>
 * </ul>
 * <p>
 * <b>分层职责（给初学者）：</b>
 * Controller 只做参数接收、HTTP 状态码包装；所有查库与字段组装放在
 * {@link HistoryQaQueryService}，避免 Controller 越写越肥。
 */
@RestController
@RequestMapping("/api/history")
public class HistoryQaController {

    /**
     * 历史问答业务查询服务。
     * 由 Spring 注入，不要在本类里 new。
     */
    @Autowired
    private HistoryQaQueryService historyQaQueryService;

    /**
     * 历史任务分页列表。
     * <p>
     * <b>业务含义：</b>返回「进行中 + 已完成」的任务，每条对应 hermes_tasks 一行。
     * 默认状态范围：{@code pending}（待开始）、{@code running}（进行中）、{@code completed}（已完成）。
     * <b>不包含</b> {@code failed}（失败任务），如需纳入请改 Service/SQL。
     * <p>
     * <b>请求示例：</b>
     * <pre>
     * GET /api/history/tasks?page=1&amp;pageSize=20
     * GET /api/history/tasks?taskType=collect&amp;status=completed
     * </pre>
     * <p>
     * <b>查询参数说明：</b>
     * <ul>
     *   <li>{@code page} —— 页码，从 1 开始，非法值由 Service 纠正为 1</li>
     *   <li>{@code pageSize} —— 每页条数，默认 20，Service 内上限 200</li>
     *   <li>{@code taskType} —— 可选业务类型筛选：
     *       前端短码 collect/expand/verify/report，或库内 account_collect 等</li>
     *   <li>{@code status} —— 可选再筛单一状态：pending / running / completed；
     *       不传则三种状态都返回</li>
     * </ul>
     * <p>
     * <b>成功响应要点（JSON）：</b>
     * page、pageSize、total、list；list 每项含 taskId、中文 taskType、statusLabel、
     * question、payload、hasAnswer、answerPreview、detailUrl 等。
     * <p>
     * <b>异常约定：</b>
     * <ul>
     *   <li>400 —— taskType 或 status 非法（Service 抛 ResponseStatusException）</li>
     *   <li>500 —— 未预期异常，body 含 error=list_history_tasks_failed</li>
     * </ul>
     *
     * @param page     页码，默认 1
     * @param pageSize 每页条数，默认 20
     * @param taskType 业务类型筛选，可空
     * @param status   任务状态筛选，可空
     * @return 200 + 分页 JSON；或 4xx/5xx + {error, detail}
     */
    @GetMapping("/tasks")
    public ResponseEntity<?> listHistoryTasks(
            @RequestParam(value = "page", defaultValue = "1") int page,
            @RequestParam(value = "pageSize", defaultValue = "20") int pageSize,
            @RequestParam(value = "taskType", required = false) String taskType,
            @RequestParam(value = "status", required = false) String status) {
        try {
            // 正常路径：交给 Service 做校验、查库、拼装驼峰字段
            return ResponseEntity.ok(historyQaQueryService.listHistoryTasks(taskType, status, page, pageSize));
        } catch (ResponseStatusException e) {
            // 业务可预期错误（参数非法等）：沿用 Service 指定的 HTTP 状态码
            return error(e);
        } catch (Exception e) {
            // 数据库断开、空指针等未预期错误：统一 500，避免把堆栈直接甩给前端
            return error(HttpStatus.INTERNAL_SERVER_ERROR, "list_history_tasks_failed", e.getMessage());
        }
    }

    /**
     * 历史任务详情。
     * <p>
     * <b>业务含义：</b>前端从列表点进某一条任务后，一次拉取本次任务的完整回顾数据：
     * <ol>
     *   <li>用户提问（content + payload）</li>
     *   <li>采集到的账号列表（优先 collect_display_records.display_fields）</li>
     *   <li>采集到的发文列表（优先 collect_display_records.display_fields）</li>
     *   <li>图片资产列表（collect_images；有存储则尽量带 dataUrl，失败不阻塞）</li>
     *   <li>模型终稿报告（优先 summary）</li>
     * </ol>
     * <p>
     * <b>请求示例：</b>
     * <pre>
     * GET /api/history/tasks/6d33a223-aad7-4d05-811e-8df53a3f9bd4
     * </pre>
     * <p>
     * <b>注意：</b>
     * <ul>
     *   <li>优先用 images[].dataUrl；没有则用 images[].imageUrl 调
     *       {@code GET /api/images/{imageId}/bytes}</li>
     *   <li>仅允许历史可见状态（pending/running/completed）；failed 返回 409</li>
     *   <li>任务不存在返回 404</li>
     * </ul>
     *
     * @param taskId 路径参数，任务唯一 ID（hermes_tasks.task_id）
     * @return 200 + 详情 JSON；或 4xx/5xx + {error, detail}
     */
    @GetMapping("/tasks/{taskId}")
    public ResponseEntity<?> getHistoryTaskDetail(@PathVariable("taskId") String taskId) {
        try {
            return ResponseEntity.ok(historyQaQueryService.getHistoryTaskDetail(taskId));
        } catch (ResponseStatusException e) {
            return error(e);
        } catch (Exception e) {
            return error(HttpStatus.INTERNAL_SERVER_ERROR, "get_history_task_failed", e.getMessage());
        }
    }

    /**
     * 删除历史任务及其在 MySQL 中的全部关联数据（含 hermes_tasks）。
     * <p>
     * <b>请求示例：</b>
     * <pre>
     * DELETE /api/history/tasks/6d33a223-aad7-4d05-811e-8df53a3f9bd4
     * </pre>
     * <p>
     * <b>成功响应：</b>{@code ok=true}、{@code taskId}、{@code deleted}（各表删除行数）。
     * <b>不删</b> HBase 中的图片二进制。
     * <p>
     * <b>异常约定：</b>404 任务不存在；400 taskId 为空；500 删除失败。
     *
     * @param taskId 路径参数，任务唯一 ID
     * @return 200 + 删除结果；或 4xx/5xx + {error, detail}
     */
    @DeleteMapping("/tasks/{taskId}")
    public ResponseEntity<?> deleteHistoryTask(@PathVariable("taskId") String taskId) {
        try {
            return ResponseEntity.ok(historyQaQueryService.deleteHistoryTask(taskId));
        } catch (ResponseStatusException e) {
            return error(e);
        } catch (Exception e) {
            return error(HttpStatus.INTERNAL_SERVER_ERROR, "delete_history_task_failed", e.getMessage());
        }
    }

    /**
     * 把 Spring 的 {@link ResponseStatusException} 转成统一错误 JSON 响应。
     * <p>
     * reason 一般是简短英文错误码，例如 task_not_found、invalid_taskType，
     * 前端可根据 error 字段做分支提示。
     */
    private ResponseEntity<Map<String, Object>> error(ResponseStatusException e) {
        HttpStatus status = e.getStatus();
        String reason = e.getReason() == null ? status.getReasonPhrase() : e.getReason();
        return error(status, reason, reason);
    }

    /**
     * 统一错误体格式，与项目其它 Controller（如 ImageAssetController）保持一致。
     *
     * @param status HTTP 状态
     * @param error  机器可读错误码
     * @param detail 可读说明（可为异常 message）
     */
    private ResponseEntity<Map<String, Object>> error(HttpStatus status, String error, String detail) {
        Map<String, Object> body = new LinkedHashMap<String, Object>();
        body.put("error", error);
        body.put("detail", detail);
        return ResponseEntity.status(status).body(body);
    }
}
