-- 视频资产表 + 全量抽帧表（本地文件 + MySQL 索引）
-- 抽帧默认间隔 3 秒；帧全部落盘；collect_videos 含整段视频分析结果

CREATE TABLE IF NOT EXISTS collect_videos (
    video_id              VARCHAR(64)   NOT NULL COMMENT '视频记录唯一ID',
    task_id               VARCHAR(64)   NOT NULL COMMENT '所属任务ID',
    task_type             VARCHAR(32)   NULL COMMENT '任务类型',
    source_type           VARCHAR(32)   NOT NULL DEFAULT 'post_video'
        COMMENT '来源：post_video/profile_video/other',
    platform              VARCHAR(32)   NULL COMMENT '来源平台',
    account_id            VARCHAR(128)  NULL COMMENT '关联账号ID',
    post_id               VARCHAR(128)  NULL COMMENT '关联发文ID，对应 collect_posts.content_id',
    profile_id            VARCHAR(128)  NULL COMMENT '关联主页账号ID',

    origin_url            TEXT          NULL COMMENT '原始视频直链URL',
    local_path            VARCHAR(512)  NULL COMMENT '本地视频路径',
    content_sha256        CHAR(64)      NULL COMMENT '视频SHA-256',
    mime_type             VARCHAR(64)   NULL COMMENT '视频MIME',
    file_size             BIGINT        NULL COMMENT '视频字节数',
    duration_sec          DECIMAL(10,2) NULL COMMENT '时长秒',
    width                 INT           NULL COMMENT '宽',
    height                INT           NULL COMMENT '高',

    storage_status        VARCHAR(16)   NOT NULL DEFAULT 'pending'
        COMMENT 'pending/downloading/stored/failed',
    analyze_status        VARCHAR(16)   NOT NULL DEFAULT 'pending'
        COMMENT 'pending/running/completed/failed/skipped',

    frame_interval_sec    DECIMAL(6,2)  NOT NULL DEFAULT 3.00 COMMENT '抽帧间隔秒，默认3',
    frame_extracted_cnt   INT           NOT NULL DEFAULT 0 COMMENT '抽帧总数',
    frame_analyzed_cnt    INT           NOT NULL DEFAULT 0 COMMENT '成功分析帧数',
    frame_stored_cnt      INT           NOT NULL DEFAULT 0 COMMENT '长期落盘帧数',

    analysis_json         JSON          NULL COMMENT '全部帧分析结果列表',
    video_analysis_text   MEDIUMTEXT    NULL COMMENT '整段视频综合分析正文',
    video_analysis_json   JSON          NULL COMMENT '整段视频结构化分析',

    error_message         VARCHAR(1000) NULL COMMENT '最近失败原因',
    retry_count           INT           NOT NULL DEFAULT 0 COMMENT '重试次数',
    created_at            DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at            DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                        ON UPDATE CURRENT_TIMESTAMP(3),
    stored_at             DATETIME(3)   NULL COMMENT '视频落盘时间',
    analyzed_at           DATETIME(3)   NULL COMMENT '分析完成时间',

    PRIMARY KEY (video_id),
    KEY idx_task (task_id),
    KEY idx_task_status (task_id, analyze_status),
    KEY idx_account (task_id, platform, account_id),
    KEY idx_post (task_id, platform, post_id),
    KEY idx_sha256 (content_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='视频资产与整段/逐帧分析结果';

CREATE TABLE IF NOT EXISTS collect_video_frames (
    frame_id              VARCHAR(64)   NOT NULL COMMENT '帧唯一ID',
    video_id              VARCHAR(64)   NOT NULL COMMENT '所属视频',
    task_id               VARCHAR(64)   NOT NULL COMMENT '所属任务',
    platform              VARCHAR(32)   NULL,
    account_id            VARCHAR(128)  NULL,
    post_id               VARCHAR(128)  NULL,

    frame_index           INT           NOT NULL COMMENT '抽帧序号，从0起',
    timestamp_sec         DECIMAL(10,2) NOT NULL COMMENT '视频内时间点（秒）',
    is_preview            TINYINT       NOT NULL DEFAULT 0
        COMMENT '是否推荐前端默认渲染：1=是（约3张）',
    sort_order            INT           NULL COMMENT '预览排序，非预览可空',

    local_path            VARCHAR(512)  NOT NULL COMMENT '帧图片本地路径',
    mime_type             VARCHAR(64)   NULL DEFAULT 'image/jpeg',
    file_size             BIGINT        NULL,
    width                 INT           NULL,
    height                INT           NULL,
    content_sha256        CHAR(64)      NULL,

    analyze_status        VARCHAR(16)   NOT NULL DEFAULT 'pending'
        COMMENT 'pending/completed/failed/skipped',
    vision_text           MEDIUMTEXT    NULL COMMENT '该帧VLM描述',
    analysis_json         JSON          NULL COMMENT '该帧结构化结果',

    created_at            DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    analyzed_at           DATETIME(3)   NULL,

    PRIMARY KEY (frame_id),
    UNIQUE KEY uk_video_frame_index (video_id, frame_index),
    KEY idx_task_video (task_id, video_id),
    KEY idx_video_preview (video_id, is_preview),
    CONSTRAINT fk_video_frames_video
        FOREIGN KEY (video_id) REFERENCES collect_videos(video_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='视频全部抽帧图及单帧分析';

INSERT IGNORE INTO schema_migrations (version) VALUES ('013_collect_videos');
