-- 04 写报 4.3 社工库核验命中薄表（ES search_country_wise）
CREATE TABLE IF NOT EXISTS collect_osint_hits (
    id               BIGINT        NOT NULL AUTO_INCREMENT COMMENT '主键',
    task_id          VARCHAR(64)   NOT NULL COMMENT '所属任务ID',
    platform         VARCHAR(32)   NULL COMMENT '关联平台',
    account_id       VARCHAR(128)  NULL COMMENT '关联账号ID',
    profile_url      TEXT          NULL COMMENT '查询用主页URL',
    source_index     VARCHAR(128)  NULL COMMENT 'ES 索引名',
    query_text       VARCHAR(1024) NULL COMMENT '实际查询文本',
    hit_count        INT           NOT NULL DEFAULT 0 COMMENT '命中条数',
    hit_json         MEDIUMTEXT    NULL COMMENT '命中整包JSON',
    tool_output_id   BIGINT        NULL COMMENT '关联 hermes_tool_outputs.id',
    created_at       DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at       DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                     ON UPDATE CURRENT_TIMESTAMP(3),

    PRIMARY KEY (id),
    UNIQUE KEY uk_task_query_index (task_id, query_text(191), source_index),
    KEY idx_task (task_id),
    KEY idx_task_account (task_id, platform, account_id),
    CONSTRAINT fk_collect_osint_hits_task
        FOREIGN KEY (task_id) REFERENCES hermes_tasks(task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='社工库核验命中（4.3 step6_osint_es）';

INSERT IGNORE INTO schema_migrations (version) VALUES ('015_collect_osint_hits');
