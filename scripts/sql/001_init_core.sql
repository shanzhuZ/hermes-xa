-- Hermes 账号智能分析平台 — 公共表 + 实体表
-- 执行：mysql -u root -p hermes < 001_init_core.sql
-- MySQL 5.7.8+ / 8.0+，字符集 utf8mb4

CREATE DATABASE IF NOT EXISTS `hermes-xa`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE `hermes-xa`;

-- 迁移版本记录（手工或 Java 维护）
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(32)  NOT NULL PRIMARY KEY COMMENT '如 001_init_core',
    applied_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SQL 迁移版本';

-- ========== 任务主表 ==========
CREATE TABLE IF NOT EXISTS hermes_tasks (
    task_id         VARCHAR(64)   NOT NULL PRIMARY KEY COMMENT 'UUID，Java 生成',
    task_type       VARCHAR(32)   NOT NULL COMMENT 'collect/cross_platform/verify/multimodal/report',
    session_id      VARCHAR(64)   NULL     COMMENT 'Hermes session_id',
    parent_task_id  VARCHAR(64)   NULL     COMMENT '上游任务 ID',
    status          VARCHAR(16)   NOT NULL DEFAULT 'pending' COMMENT 'pending/running/waiting_user/completed/failed',
    subject_json    JSON          NULL     COMMENT '种子账号、本人参照等',
    summary_json    JSON          NULL     COMMENT '任务结束统计摘要',
    error_message   TEXT          NULL,
    created_by      VARCHAR(64)   NULL     COMMENT '操作人',
    created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    KEY idx_type_status (task_type, status),
    KEY idx_session (session_id),
    KEY idx_parent (parent_task_id),
    KEY idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='任务主表';

-- ========== Clarify / 确认点 ==========
CREATE TABLE IF NOT EXISTS hermes_task_steps (
    id            BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id       VARCHAR(64)   NOT NULL,
    step_key      VARCHAR(64)   NOT NULL COMMENT '如 collect.time_range',
    step_order    INT           NOT NULL DEFAULT 0,
    source        VARCHAR(16)   NOT NULL DEFAULT 'clarify' COMMENT 'clarify/java_ui/system_confirm',
    question      TEXT          NOT NULL,
    options_json  JSON          NULL,
    user_choice   TEXT          NULL,
    chosen_at     DATETIME(3)   NULL,
    extra_json    JSON          NULL,
    created_at    DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_task (task_id),
    KEY idx_task_step (task_id, step_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='确认点与用户选择';

-- ========== 用户原始提问 ==========
CREATE TABLE IF NOT EXISTS hermes_user_questions (
    question_id    VARCHAR(64)  NOT NULL PRIMARY KEY,
    task_id        VARCHAR(64)  NOT NULL,
    session_id     VARCHAR(64)  NOT NULL,
    user_question  TEXT         NOT NULL,
    asked_at       DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户原始提问';

-- ========== MCP 工具原始输出（审计真源） ==========
CREATE TABLE IF NOT EXISTS hermes_tool_outputs (
    id            BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id       VARCHAR(64)   NOT NULL,
    question_id   VARCHAR(64)   NULL,
    mcp_server    VARCHAR(64)   NULL,
    tool_name     VARCHAR(128)  NOT NULL,
    tool_args     JSON          NULL,
    tool_output   LONGTEXT      NOT NULL COMMENT '完整 JSON，不截断',
    tool_call_id  VARCHAR(128)  NOT NULL,
    duration_ms   INT           NULL,
    status        VARCHAR(16)   NULL COMMENT 'success/error',
    executed_at   DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_tool_call (tool_call_id),
    KEY idx_task (task_id),
    KEY idx_tool (tool_name),
    KEY idx_executed (executed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='MCP 工具调用原始输出';

-- ========== 跨业务实体 ==========
CREATE TABLE IF NOT EXISTS biz_entities (
    entity_id      VARCHAR(64)   NOT NULL PRIMARY KEY,
    display_name   VARCHAR(128)  NULL,
    created_at     DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    extra_json     JSON          NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='自然人/分析主体';

CREATE TABLE IF NOT EXISTS biz_entity_accounts (
    id              BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    entity_id       VARCHAR(64)   NOT NULL,
    platform        VARCHAR(32)   NOT NULL,
    account_id      VARCHAR(128)  NOT NULL,
    account_handle  VARCHAR(256)  NULL,
    role            VARCHAR(32)   NULL COMMENT 'seed/candidate/verified_self/verified_not_self',
    profile_json    JSON          NULL,
    extra_json      JSON          NULL,
    created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_platform_account (platform, account_id),
    KEY idx_entity (entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实体下的平台账号';

INSERT IGNORE INTO schema_migrations (version) VALUES ('001_init_core');
