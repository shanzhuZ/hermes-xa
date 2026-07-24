-- 视频抽帧进 HBase：复用表 collect_image_bytes，RowKey 前缀 frm:
-- 本地 frames/*.jpg 上传成功后可删除；source.mp4 仍本地

ALTER TABLE collect_video_frames
    ADD COLUMN hbase_row_key VARCHAR(128) NULL
        COMMENT 'HBase帧图RowKey，如 frm:{taskId}:{sha16}' AFTER local_path,
    ADD COLUMN storage_status VARCHAR(16) NOT NULL DEFAULT 'pending'
        COMMENT 'pending/stored/failed' AFTER hbase_row_key;

ALTER TABLE collect_video_frames
    MODIFY COLUMN local_path VARCHAR(512) NULL
        COMMENT '帧本地路径（上传HBase成功后可清空）';

INSERT IGNORE INTO schema_migrations (version) VALUES ('014_video_frames_hbase');
