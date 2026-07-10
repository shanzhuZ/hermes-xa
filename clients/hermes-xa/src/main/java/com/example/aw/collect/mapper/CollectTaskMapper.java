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
                            @Param("content") String content);

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

    void markTaskFailed(@Param("taskId") String taskId, @Param("errorMessage") String errorMessage);

    Map<String, Object> selectUserInput(@Param("taskId") String taskId);

    Map<String, Object> selectSummary(@Param("taskId") String taskId);

    List<Map<String, Object>> selectPhaseSteps(@Param("taskId") String taskId);

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

    Map<String, Object> selectLatestAssistantReply(@Param("taskId") String taskId);
}
