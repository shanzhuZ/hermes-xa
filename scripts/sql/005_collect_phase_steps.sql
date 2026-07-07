-- 01 账号采集 — 前端深度模式步骤表（密塔式进度树）
-- 执行：mysql -h 127.0.0.1 -P 3306 -u root -p --default-character-set=utf8mb4 hermes-xa < 005_collect_phase_steps.sql

USE `hermes-xa`;

CREATE TABLE IF NOT EXISTS collect_phase_steps (
    id              BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id         VARCHAR(64)   NOT NULL COMMENT '关联任务 ID',
    step_key        VARCHAR(64)   NOT NULL COMMENT '步骤键，如 step1_seed / step6_post_twitter',
    parent_step_key VARCHAR(64)   NULL     COMMENT '父步骤键，如 step6_posts 的子步骤',
    step_order      INT           NOT NULL COMMENT '同级排序，越小越靠前',
    title           VARCHAR(256)  NOT NULL COMMENT '前端展示标题（中文）',
    status          VARCHAR(16)   NOT NULL DEFAULT 'pending' COMMENT 'pending/running/completed/failed/skipped',
    progress_pct    TINYINT       NULL     COMMENT '0-100 可选进度',
    message         TEXT          NULL     COMMENT '步骤说明或结果摘要',
    payload_json    JSON          NULL     COMMENT '前端渲染用结构化摘要',
    started_at      DATETIME(3)   NULL     COMMENT '开始时间',
    finished_at     DATETIME(3)   NULL     COMMENT '结束时间',
    created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
    updated_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
    UNIQUE KEY uk_task_step (task_id, step_key),
    KEY idx_task_order (task_id, step_order),
    KEY idx_task_parent (task_id, parent_step_key),
    KEY idx_task_status (task_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='01采集阶段步骤表：供前端深度模式渲染';

INSERT IGNORE INTO schema_migrations (version) VALUES ('005_collect_phase_steps');
