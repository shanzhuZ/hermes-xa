-- 展示层增加 account_id，关联人物资料与发文
ALTER TABLE collect_display_records
    ADD COLUMN account_id VARCHAR(128) NULL COMMENT '平台账号 ID，关联 collect_profiles / collect_posts' AFTER platform;

UPDATE collect_display_records d
JOIN collect_profiles p ON d.source_table = 'collect_profiles' AND d.source_ref = CAST(p.id AS CHAR) AND d.task_id = p.task_id
SET d.account_id = p.account_id
WHERE d.account_id IS NULL;

UPDATE collect_display_records d
JOIN collect_posts p ON d.source_table = 'collect_posts' AND d.source_ref = CAST(p.id AS CHAR) AND d.task_id = p.task_id
SET d.account_id = p.account_id
WHERE d.account_id IS NULL;

UPDATE collect_display_records d
JOIN collect_validated_accounts v ON d.source_table = 'collect_validated_accounts' AND d.source_ref = CAST(v.id AS CHAR) AND d.task_id = v.task_id
SET d.account_id = v.account_id
WHERE d.account_id IS NULL;

UPDATE collect_display_records d
JOIN collect_identity_streams s ON d.source_table = 'collect_identity_streams' AND d.source_ref = s.stream_id AND d.task_id = s.task_id
SET d.account_id = s.source_account_id
WHERE d.account_id IS NULL;

ALTER TABLE collect_display_records
    ADD KEY idx_task_account (task_id, account_id);

INSERT IGNORE INTO schema_migrations (version) VALUES ('010_collect_display_records_account_id');
