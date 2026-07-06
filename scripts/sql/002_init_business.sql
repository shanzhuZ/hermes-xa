-- Hermes 账号智能分析平台 — 五类业务表
-- 依赖：001_init_core.sql
-- 执行：mysql -u root -p hermes < 002_init_business.sql

USE `hermes-xa`;

-- ==================== 01 账号信息采集 ====================

CREATE TABLE IF NOT EXISTS collect_profiles (
    id              BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(64)   NOT NULL,
    platform        VARCHAR(32)   NOT NULL,
    account_id      VARCHAR(128)  NOT NULL,
    account_handle  VARCHAR(256)  NULL,
    profile_json    JSON          NULL,
    tool_output_id  BIGINT        NULL,
    collected_at    DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    extra_json      JSON          NULL COMMENT 'collect_options: 关键词、时间范围等',
    KEY idx_task (task_id),
    KEY idx_account (platform, account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='01-采集账号资料';

CREATE TABLE IF NOT EXISTS collect_posts (
    id               BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id          VARCHAR(64)   NOT NULL,
    platform         VARCHAR(32)   NOT NULL,
    account_id       VARCHAR(128)  NOT NULL,
    post_id          VARCHAR(128)  NOT NULL,
    post_url         VARCHAR(512)  NULL,
    content_text     TEXT          NULL,
    posted_at        DATETIME(3)   NULL,
    media_json       JSON          NULL,
    stats_json       JSON          NULL,
    matched_keywords JSON          NULL,
    raw_json         JSON          NULL,
    tool_output_id   BIGINT        NULL,
    extra_json       JSON          NULL,
    created_at       DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_task (task_id),
    KEY idx_post (platform, post_id),
    KEY idx_account (platform, account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='01-采集博文/主内容';

CREATE TABLE IF NOT EXISTS collect_comments (
    id               BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id          VARCHAR(64)   NOT NULL,
    platform         VARCHAR(32)   NOT NULL,
    account_id       VARCHAR(128)  NULL COMMENT '评论者',
    post_id          VARCHAR(128)  NOT NULL,
    comment_id       VARCHAR(128)  NOT NULL,
    content_text     TEXT          NULL,
    commented_at     DATETIME(3)   NULL,
    raw_json         JSON          NULL,
    tool_output_id   BIGINT        NULL,
    extra_json       JSON          NULL,
    created_at       DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_task (task_id),
    KEY idx_post (platform, post_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='01-采集评论';

-- ==================== 02 跨平台账号收集 ====================

CREATE TABLE IF NOT EXISTS cross_platform_candidates (
    id               BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id          VARCHAR(64)   NOT NULL,
    entity_id        VARCHAR(64)   NULL,
    platform         VARCHAR(32)   NOT NULL,
    account_id       VARCHAR(128)  NOT NULL,
    account_handle   VARCHAR(256)  NULL,
    confidence       DECIMAL(5,4)  NULL,
    evidence_json    JSON          NULL,
    match_strategy   VARCHAR(64)   NULL,
    status           VARCHAR(16)   NOT NULL DEFAULT 'candidate' COMMENT 'candidate/weak/excluded',
    profile_json     JSON          NULL,
    tool_output_id   BIGINT        NULL,
    extra_json       JSON          NULL,
    created_at       DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_task (task_id),
    KEY idx_entity (entity_id),
    KEY idx_confidence (confidence)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='02-跨平台候选账号';

-- ==================== 03 账号核查 ====================

CREATE TABLE IF NOT EXISTS verify_subjects (
    id                    BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id               VARCHAR(64)   NOT NULL,
    name                  VARCHAR(128)  NULL,
    anchor_platform       VARCHAR(32)   NULL,
    anchor_account_id     VARCHAR(128)  NULL,
    anchor_handle         VARCHAR(256)  NULL,
    reference_photos_json JSON          NULL,
    org_title             VARCHAR(256)  NULL,
    verify_options_json   JSON          NULL,
    extra_json            JSON          NULL,
    created_at            DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='03-本人参照信息';

CREATE TABLE IF NOT EXISTS verify_account_inputs (
    id              BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(64)   NOT NULL,
    platform        VARCHAR(32)   NOT NULL,
    account_id      VARCHAR(128)  NOT NULL,
    account_handle  VARCHAR(256)  NULL,
    source          VARCHAR(32)   NULL COMMENT 'manual/from_cross_platform/from_collect',
    input_order     INT           NOT NULL DEFAULT 0,
    created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_task_account (task_id, platform, account_id),
    KEY idx_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='03-待核查账号清单';

CREATE TABLE IF NOT EXISTS verify_account_results (
    id              BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(64)   NOT NULL,
    platform        VARCHAR(32)   NOT NULL,
    account_id      VARCHAR(128)  NOT NULL,
    account_handle  VARCHAR(256)  NULL,
    verdict         VARCHAR(32)   NULL COMMENT 'self_authentic/not_self/impersonation_suspected/insufficient_data',
    confidence      DECIMAL(5,4)  NULL,
    summary         TEXT          NULL,
    user_override   TINYINT(1)    NOT NULL DEFAULT 0,
    final_verdict   VARCHAR(32)   NULL,
    tool_output_id  BIGINT        NULL,
    extra_json      JSON          NULL,
    created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_task_account (task_id, platform, account_id),
    KEY idx_verdict (task_id, final_verdict)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='03-逐账号核查结论';

CREATE TABLE IF NOT EXISTS verify_evidence (
    evidence_id       VARCHAR(64)  NOT NULL PRIMARY KEY,
    task_id           VARCHAR(64)  NOT NULL,
    result_id         BIGINT       NULL,
    evidence_type     VARCHAR(32)  NULL,
    description       TEXT         NULL,
    source_ref_json   JSON         NULL,
    extra_json        JSON         NULL,
    created_at        DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_task (task_id),
    KEY idx_result (result_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='03-核查证据条目';

-- ==================== 04 账号多模态分析 ====================

CREATE TABLE IF NOT EXISTS multimodal_findings (
    id              BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id         VARCHAR(64)   NOT NULL,
    platform        VARCHAR(32)   NULL,
    account_id      VARCHAR(128)  NULL,
    finding_type    VARCHAR(32)   NOT NULL COMMENT 'text_summary/image/cross_modal/video',
    post_id         VARCHAR(128)  NULL,
    media_url       VARCHAR(512)  NULL,
    severity        VARCHAR(16)   NULL COMMENT 'low/medium/high',
    labels_json     JSON          NULL,
    description     TEXT          NULL,
    ocr_text        TEXT          NULL,
    confidence      DECIMAL(5,4)  NULL,
    tool_output_id  BIGINT        NULL,
    extra_json      JSON          NULL,
    created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    KEY idx_task (task_id),
    KEY idx_type (task_id, finding_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='04-多模态分析发现';

-- ==================== 05 账号核查报告 ====================

CREATE TABLE IF NOT EXISTS verify_reports (
    id                  BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id             VARCHAR(64)   NOT NULL,
    title               VARCHAR(256)  NULL,
    template            VARCHAR(32)   NULL COMMENT 'brief/standard/deep',
    audience            VARCHAR(32)   NULL COMMENT 'technical/business/external',
    risk_level          VARCHAR(16)   NULL,
    verdict_summary     TEXT          NULL,
    sections_json       JSON          NULL,
    upstream_refs_json  JSON          NULL COMMENT '关联上游 task_id 列表',
    version             INT           NOT NULL DEFAULT 1,
    status              VARCHAR(16)   NOT NULL DEFAULT 'draft' COMMENT 'draft/final',
    extra_json          JSON          NULL,
    created_at          DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at          DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='05-核查报告主表';

CREATE TABLE IF NOT EXISTS verify_report_contents (
    id            BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY,
    report_id     BIGINT        NOT NULL,
    version       INT           NOT NULL,
    format        VARCHAR(16)   NOT NULL COMMENT 'markdown/html/json',
    content       LONGTEXT      NOT NULL,
    generated_at  DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    UNIQUE KEY uk_report_version_format (report_id, version, format),
    KEY idx_report (report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='05-报告正文（多版本）';

INSERT IGNORE INTO schema_migrations (version) VALUES ('002_init_business');
