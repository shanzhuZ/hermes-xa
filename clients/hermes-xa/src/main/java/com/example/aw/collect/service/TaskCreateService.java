package com.example.aw.collect.service;

/**
 * 各业务预建 hermes_tasks + collect_phase_steps。
 */
public interface TaskCreateService {

    /**
     * 库内 task_type，如 account_collect / account_expand。
     */
    String dbTaskType();

    /**
     * Java 预建 pending 任务，返回 taskId。
     */
    String createPendingTask(String taskId, String sessionId, String userMessage);
}
