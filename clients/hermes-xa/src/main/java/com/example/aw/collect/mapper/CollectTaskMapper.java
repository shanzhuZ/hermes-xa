package com.example.aw.collect.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;

import java.util.List;
import java.util.Map;

/**
 * 01 账号采集 — hermes_tasks / 步骤 / 对话 / 工具输出查询与预建任务。
 */
@Mapper
public interface CollectTaskMapper {

    Map<String, Object> selectTaskById(@Param("taskId") String taskId);

    Map<String, Object> selectActiveTaskBySessionId(@Param("sessionId") String sessionId);

    List<Map<String, Object>> selectTasksBySessionId(@Param("sessionId") String sessionId);

    void insertTask(@Param("taskId") String taskId,
                    @Param("sessionId") String sessionId,
                    @Param("taskType") String taskType,
                    @Param("crossPlatform") int crossPlatform,
                    @Param("seedJson") String seedJson);

    void insertUserDialogue(@Param("taskId") String taskId,
                            @Param("sessionId") String sessionId,
                            @Param("content") String content,
                            @Param("payloadJson") String payloadJson);

    long countAllDialogues(@Param("dbTaskType") String dbTaskType);

    List<Map<String, Object>> selectDialoguesPage(@Param("offset") int offset,
                                                  @Param("limit") int limit,
                                                  @Param("dbTaskType") String dbTaskType);

    /** 按任务类型统计历史对话条数（与 selectDialoguesPage 同一折叠规则） */
    List<Map<String, Object>> countDialoguesGroupByTaskType();

    void insertPhaseStep(@Param("taskId") String taskId,
                         @Param("stepKey") String stepKey,
                         @Param("parentStepKey") String parentStepKey,
                         @Param("stepOrder") int stepOrder,
                         @Param("stepNode") String stepNode,
                         @Param("title") String title);

    void updateStepSkipped(@Param("taskId") String taskId,
                           @Param("stepKey") String stepKey,
                           @Param("message") String message);

    void updateStepStatus(@Param("taskId") String taskId,
                          @Param("stepKey") String stepKey,
                          @Param("status") String status,
                          @Param("message") String message);

    /** 单步状态；无行返回 null */
    String selectStepStatus(@Param("taskId") String taskId, @Param("stepKey") String stepKey);

    /**
     * 粗同步：仅 pending/running → running，不覆盖 skipped/failed/completed。
     */
    int updateStepStatusCoarseRunning(@Param("taskId") String taskId,
                                      @Param("stepKey") String stepKey,
                                      @Param("message") String message);

    /**
     * 粗同步：仅 pending/running → completed（白名单工具）；不覆盖 skipped/failed。
     */
    int updateStepStatusCoarseCompleted(@Param("taskId") String taskId,
                                        @Param("stepKey") String stepKey,
                                        @Param("message") String message);

    void markTaskFailed(@Param("taskId") String taskId, @Param("errorMessage") String errorMessage);

    /** 任务置为 completed（不覆盖 failed） */
    int markTaskCompleted(@Param("taskId") String taskId, @Param("phase") String phase);

    /** 思考终稿（msg_type=thoughts_final） */
    Map<String, Object> selectThoughtsFinal(@Param("taskId") String taskId);

    void insertThoughtsFinal(@Param("taskId") String taskId,
                             @Param("sessionId") String sessionId,
                             @Param("content") String content);

    int updateThoughtsFinal(@Param("taskId") String taskId, @Param("content") String content);

    /**
     * 查询任务首条用户提问（msg_type=user_input）。
     * 返回 content、payload_json、created_at；历史详情的 question 节点用此方法。
     */
    Map<String, Object> selectUserInput(@Param("taskId") String taskId);

    /**
     * 查询任务业务终稿（msg_type=summary）。
     * 思考区 thoughts_final 不在此列；终稿统一看本方法或 TaskFinalAnswerQueryService。
     */
    Map<String, Object> selectSummary(@Param("taskId") String taskId);

    /**
     * 统计历史问答可见任务数。
     * <p>
     * 基础范围：status IN (pending, running, completed)。
     * dbTaskType 非空时按 hermes_tasks.task_type 过滤；
     * status 非空时再精确到单一状态（仍须属于上述三种之一，由调用方保证）。
     *
     * @param dbTaskType 库内业务类型，如 account_collect；可空
     * @param status     pending/running/completed；可空表示三种都算
     */
    long countHistoryTasks(@Param("dbTaskType") String dbTaskType,
                           @Param("status") String status);

    /**
     * 分页查询历史任务列表行。
     * <p>
     * 除任务主字段外，还通过子查询附带：
     * question（首条 user_input.content）、
     * payload_json（首条 user_input.payload_json）、
     * answer_preview（最新 summary.content）。
     * 排序：created_at DESC。供 HistoryQaQueryService.listHistoryTasks 使用。
     *
     * @param dbTaskType 库内业务类型，可空
     * @param status     单一状态，可空
     * @param offset     偏移量 = (page-1)*pageSize
     * @param limit      每页条数
     */
    List<Map<String, Object>> selectHistoryTasksPage(@Param("dbTaskType") String dbTaskType,
                                                     @Param("status") String status,
                                                     @Param("offset") int offset,
                                                     @Param("limit") int limit);

    /**
     * 统计某任务下 collect_profiles 条数（历史详情 counts.profiles）。
     */
    long countProfilesByTaskId(@Param("taskId") String taskId);

    /**
     * 统计某任务下 collect_posts 条数（历史详情 counts.posts）。
     */
    long countPostsByTaskId(@Param("taskId") String taskId);

    List<Map<String, Object>> selectPhaseSteps(@Param("taskId") String taskId);

    /**
     * 仅更新进度百分比（值未变则 0 行），不碰 status / updated_at，避免与 Hook 抢写。
     */
    int updateStepProgressPctIfChanged(@Param("taskId") String taskId,
                                       @Param("stepKey") String stepKey,
                                       @Param("progressPct") int progressPct);

    List<Map<String, Object>> selectToolOutputs(@Param("taskId") String taskId);

    List<Map<String, Object>> selectSuccessToolOutputsByTaskAndPhase(
            @Param("taskId") String taskId, @Param("phase") String phase);

    Map<String, Object> selectToolOutputById(@Param("taskId") String taskId, @Param("toolId") long toolId);

    Map<String, Object> selectPhaseStep(@Param("taskId") String taskId, @Param("stepKey") String stepKey);

    Map<String, Object> selectTaskSeedJson(@Param("taskId") String taskId);

    List<Map<String, Object>> selectProfilesByTaskId(@Param("taskId") String taskId);

    List<Map<String, Object>> selectProfilesByTaskAndPlatform(
            @Param("taskId") String taskId, @Param("platform") String platform);

    List<Map<String, Object>> selectCrossPlatformCandidates(@Param("taskId") String taskId);

    List<Map<String, Object>> selectIdentityStreams(@Param("taskId") String taskId);

    List<Map<String, Object>> selectIdentityStreamsByType(
            @Param("taskId") String taskId, @Param("streamType") String streamType);

    List<Map<String, Object>> selectValidatedAccounts(@Param("taskId") String taskId);

    List<Map<String, Object>> selectPostsByTaskId(@Param("taskId") String taskId);

    List<Map<String, Object>> selectPostsByTaskAndPlatform(
            @Param("taskId") String taskId, @Param("platform") String platform);

    List<Map<String, Object>> selectDisplayRecordsByStepKey(
            @Param("taskId") String taskId, @Param("stepKey") String stepKey);

    /** step_key 前缀匹配（如 step3_profile_ / step3_post_），用于 03 核查等无父汇总节点的任务 */
    List<Map<String, Object>> selectDisplayRecordsByStepKeyPrefix(
            @Param("taskId") String taskId, @Param("stepKeyPrefix") String stepKeyPrefix);

    Map<String, Object> selectLatestAssistantReply(@Param("taskId") String taskId);

    // ---------- 按 taskId 级联删除（须先删子表，最后删 hermes_tasks） ----------

    /** 删除 collect_images（含 FK → hermes_tasks，必须先于任务主表删除） */
    int deleteCollectImagesByTaskId(@Param("taskId") String taskId);

    int deleteCollectDisplayRecordsByTaskId(@Param("taskId") String taskId);

    int deleteCollectIdentityStreamsByTaskId(@Param("taskId") String taskId);

    int deleteCollectPhaseStepsByTaskId(@Param("taskId") String taskId);

    int deleteCollectPostsByTaskId(@Param("taskId") String taskId);

    int deleteCollectProfilesByTaskId(@Param("taskId") String taskId);

    int deleteCollectTaskSummariesByTaskId(@Param("taskId") String taskId);

    int deleteCollectValidatedAccountsByTaskId(@Param("taskId") String taskId);

    int deleteCrossPlatformCandidatesByTaskId(@Param("taskId") String taskId);

    int deleteHermesToolOutputsByTaskId(@Param("taskId") String taskId);

    int deleteHermesUserDialoguesByTaskId(@Param("taskId") String taskId);

    /** 删除任务主表 hermes_tasks 自身；须在所有子表删除之后调用 */
    int deleteHermesTaskById(@Param("taskId") String taskId);

    // ---------- 动态流程图（custom flow） ----------

    /** 按 step_key 删除该任务下未出现在 keep 列表中的步骤（replace 模式用） */
    int deletePhaseStepsNotIn(@Param("taskId") String taskId,
                              @Param("keepKeys") List<String> keepKeys);

    /** 删除某任务全部步骤 */
    int deleteAllPhaseStepsByTaskId(@Param("taskId") String taskId);

    /**
     * 插入或更新步骤元数据（title/order/parent/node）；status 仅在行新建时用 pending，
     * 已存在行默认不改 status（由 begin/finish 管）。
     */
    int upsertPhaseStepMeta(@Param("taskId") String taskId,
                            @Param("stepKey") String stepKey,
                            @Param("parentStepKey") String parentStepKey,
                            @Param("stepOrder") int stepOrder,
                            @Param("stepNode") String stepNode,
                            @Param("title") String title);

    /**
     * 更新步骤状态（含 finished_at 规则，对齐 Python set_step_status）。
     */
    int updatePhaseStepLifecycle(@Param("taskId") String taskId,
                                 @Param("stepKey") String stepKey,
                                 @Param("status") String status,
                                 @Param("message") String message);
}
