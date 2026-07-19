-- 图片资产表：原图 HBase 索引 + OCR/Vision 分析结果
CREATE TABLE IF NOT EXISTS collect_images (
    image_id           VARCHAR(64)   NOT NULL COMMENT '图片记录唯一ID',
    task_id            VARCHAR(64)   NOT NULL COMMENT '所属任务ID',
    task_type          VARCHAR(32)   NULL COMMENT '任务类型，便于按业务查询',
    source_type        VARCHAR(32)   NOT NULL COMMENT '来源：profile_avatar/profile_cover/post_media',
    platform           VARCHAR(32)   NULL COMMENT '来源平台',
    account_id         VARCHAR(128)  NULL COMMENT '关联账号ID',
    post_id            VARCHAR(128)  NULL COMMENT '关联发文ID，对应 collect_posts.content_id',
    profile_id         VARCHAR(128)  NULL COMMENT '关联主页ID，通常为 collect_profiles.account_id',
    origin_url         TEXT          NULL COMMENT '原始图片URL',
    hbase_row_key      VARCHAR(128)  NULL COMMENT 'HBase原图RowKey',
    content_sha256     CHAR(64)      NULL COMMENT '原图SHA-256，用于幂等和去重',
    mime_type          VARCHAR(64)   NULL COMMENT '图片MIME类型',
    file_size          BIGINT        NULL COMMENT '原图字节数',
    width              INT           NULL COMMENT '图片宽度',
    height             INT           NULL COMMENT '图片高度',
    storage_status     VARCHAR(16)   NOT NULL DEFAULT 'pending'
        COMMENT '存储状态：pending/downloading/stored/failed',
    analyze_status     VARCHAR(16)   NOT NULL DEFAULT 'pending'
        COMMENT '分析状态：pending/running/completed/failed/skipped',
    ocr_text           MEDIUMTEXT    NULL COMMENT 'OCR识别文本',
    vision_text        MEDIUMTEXT    NULL COMMENT 'Vision图片描述',
    analysis_json      JSON          NULL COMMENT '结构化分析结果',
    error_message      VARCHAR(1000) NULL COMMENT '最近一次失败原因',
    retry_count        INT           NOT NULL DEFAULT 0 COMMENT '处理重试次数',
    tool_output_id     BIGINT        NULL COMMENT '关联OCR/Vision工具输出ID',
    created_at         DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at         DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                      ON UPDATE CURRENT_TIMESTAMP(3),
    stored_at          DATETIME(3)   NULL COMMENT '原图写入HBase时间',
    analyzed_at        DATETIME(3)   NULL COMMENT '分析完成时间',

    PRIMARY KEY (image_id),
    KEY idx_task (task_id),
    KEY idx_task_status (task_id, analyze_status),
    KEY idx_task_source (task_id, source_type),
    KEY idx_account (task_id, platform, account_id),
    KEY idx_post (task_id, platform, post_id),
    KEY idx_sha256 (content_sha256),
    KEY idx_storage_status (storage_status),
    CONSTRAINT fk_collect_images_task
        FOREIGN KEY (task_id) REFERENCES hermes_tasks(task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='图片资产、业务关联及OCR/Vision分析结果';

INSERT IGNORE INTO schema_migrations (version) VALUES ('012_collect_images');
