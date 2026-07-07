-- 01 账号采集 — collect_phase_steps 新增 step_node（前端链路节点）
-- 执行：
-- mysql -h 127.0.0.1 -P 3306 -u root -p --default-character-set=utf8mb4 hermes-xa < 006_collect_phase_steps_step_node.sql

USE `hermes-xa`;

ALTER TABLE collect_phase_steps
    ADD COLUMN step_node VARCHAR(16) NULL COMMENT '前端链路节点，如 1 / 4 / 4.1 / 6.2' AFTER step_order;

UPDATE collect_phase_steps
SET step_node = CASE
    WHEN step_key = 'step1_seed' THEN '1'
    WHEN step_key = 'step2_cross_platform' THEN '2'
    WHEN step_key = 'step3_profiles' THEN '3'
    WHEN step_key = 'step3_streams' THEN '4'
    WHEN step_key = 'step4_text_compare' THEN '4.1'
    WHEN step_key = 'step4_image_compare' THEN '4.2'
    WHEN step_key = 'step5_validated' THEN '5'
    WHEN step_key = 'step6_posts' THEN '6'
    WHEN step_key = 'step6_post_twitter' THEN '6.1'
    WHEN step_key = 'step6_post_youtube' THEN '6.2'
    WHEN step_key = 'step6_post_weibo' THEN '6.3'
    WHEN step_key = 'step6_post_instagram' THEN '6.4'
    WHEN step_key = 'step6_post_tiktok' THEN '6.5'
    WHEN step_key = 'step6_post_telegram' THEN '6.6'
    WHEN step_key = 'step6_post_facebook' THEN '6.7'
    WHEN step_key = 'step6_post_github' THEN '6.8'
    WHEN step_key = 'step6_post_bilibili' THEN '6.9'
    ELSE step_node
END
WHERE step_node IS NULL OR step_node = '';

INSERT IGNORE INTO schema_migrations (version) VALUES ('006_collect_phase_steps_step_node');
