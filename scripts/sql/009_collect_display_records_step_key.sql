-- 展示层增加 step_key，按步骤直接查询
ALTER TABLE collect_display_records
    ADD COLUMN step_key VARCHAR(64) NULL COMMENT '步骤键，与 collect_phase_steps.step_key 一致' AFTER task_id;

-- 旧数据先按 data_type 回填（后续可用 backfill_display_records.py 精修）
UPDATE collect_display_records SET step_key = 'step2_cross_platform'
WHERE step_key IS NULL AND data_type = 'cross_platform_candidates';

UPDATE collect_display_records SET step_key = 'step3_profiles'
WHERE step_key IS NULL AND data_type = 'collect_profiles';

UPDATE collect_display_records SET step_key = 'step3_streams'
WHERE step_key IS NULL AND data_type = 'collect_identity_streams' AND (stream_type IS NULL OR stream_type = '');

UPDATE collect_display_records SET step_key = 'step3_streams'
WHERE step_key IS NULL AND data_type = 'collect_identity_streams' AND stream_type = 'text';

UPDATE collect_display_records SET step_key = 'step3_streams'
WHERE step_key IS NULL AND data_type = 'collect_identity_streams' AND stream_type = 'image';

UPDATE collect_display_records SET step_key = 'step5_validated'
WHERE step_key IS NULL AND data_type = 'collect_validated_accounts';

UPDATE collect_display_records SET step_key = 'step6_posts'
WHERE step_key IS NULL AND data_type = 'collect_posts';

UPDATE collect_display_records SET step_key = 'unknown' WHERE step_key IS NULL;

ALTER TABLE collect_display_records
    MODIFY COLUMN step_key VARCHAR(64) NOT NULL COMMENT '步骤键';

ALTER TABLE collect_display_records
    DROP INDEX uk_source;

ALTER TABLE collect_display_records
    ADD UNIQUE KEY uk_step_source (task_id, step_key, source_table, source_ref);

ALTER TABLE collect_display_records
    ADD KEY idx_task_step (task_id, step_key);

INSERT IGNORE INTO schema_migrations (version) VALUES ('009_collect_display_records_step_key');
