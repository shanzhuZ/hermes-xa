-- 前端展示层：与业务表双写，存 [{label, value}] 格式
CREATE TABLE IF NOT EXISTS collect_display_records (
    id              BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id         VARCHAR(64)   NOT NULL COMMENT '关联任务 ID',
    step_key        VARCHAR(64)   NOT NULL COMMENT '步骤键，与 collect_phase_steps.step_key 一致',
    data_type       VARCHAR(64)   NOT NULL COMMENT '数据类型：collect_profiles / collect_posts 等',
    source_table    VARCHAR(64)   NOT NULL COMMENT '来源业务表名',
    source_ref      VARCHAR(128)  NOT NULL COMMENT '来源主键（数字 id 或 stream_id）',
    platform        VARCHAR(32)   NULL     COMMENT '平台，便于 step6_post_* 过滤',
    stream_type     VARCHAR(16)   NULL     COMMENT '流类型 text/image，仅 identity_streams',
    record_title    VARCHAR(256)  NULL     COMMENT '卡片标题',
    display_fields  JSON          NOT NULL COMMENT '展示字段 [{label,value,type?}]',
    created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
    updated_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
    UNIQUE KEY uk_step_source (task_id, step_key, source_table, source_ref),
    KEY idx_task_type (task_id, data_type),
    KEY idx_task_step (task_id, step_key),
    KEY idx_task_platform (task_id, platform),
    KEY idx_task_stream (task_id, stream_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='前端展示用记录，与业务表双写';

INSERT IGNORE INTO schema_migrations (version) VALUES ('008_collect_display_records');
