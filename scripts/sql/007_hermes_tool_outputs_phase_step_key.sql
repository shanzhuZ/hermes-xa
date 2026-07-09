-- 01 账号采集 — hermes_tool_outputs.phase 改为存 collect_phase_steps.step_key
-- 执行：mysql -h 127.0.0.1 -P 3306 -u root -p --default-character-set=utf8mb4 hermes-xa < 007_hermes_tool_outputs_phase_step_key.sql

USE `hermes-xa`;

ALTER TABLE hermes_tool_outputs
    MODIFY COLUMN phase VARCHAR(64) NULL COMMENT '工具调用归属步骤键，与 collect_phase_steps.step_key 一致，如 step1_seed / step6_post_twitter';

UPDATE hermes_tool_outputs
SET phase = CASE tool_name
    WHEN 'mcp_twitter_get_user_info' THEN 'step1_seed'
    WHEN 'mcp_maigret_collect_accounts' THEN 'step2_cross_platform'
    WHEN 'mcp_youtube_get_channel_stats' THEN 'step3_profiles'
    WHEN 'mcp_weibo_get_profile' THEN 'step3_profiles'
    WHEN 'mcp_apify_apify__instagram_scraper' THEN 'step3_profiles'
    WHEN 'mcp_apify_clockworks__tiktok_scraper' THEN 'step3_profiles'
    WHEN 'mcp_apify_vujeen__telegram_channel_scraper' THEN 'step3_profiles'
    WHEN 'mcp_apify_headlessagent__facebook_profile_post_scraper' THEN 'step3_profiles'
    WHEN 'mcp_apify_knotless_cadence__github_profile_scraper' THEN 'step3_profiles'
    WHEN 'mcp_apify_get_dataset_items' THEN 'step3_profiles'
    WHEN 'mcp_ocr_perform_ocr' THEN 'step4_image_compare'
    WHEN 'mcp_vision_analyze' THEN 'step4_image_compare'
    WHEN 'vision_analyze' THEN 'step4_image_compare'
    WHEN 'mcp_twitter_get_user_tweets' THEN 'step6_post_twitter'
    WHEN 'mcp_youtube_analyze_channel_videos' THEN 'step6_post_youtube'
    WHEN 'mcp_weibo_get_user_feeds' THEN 'step6_post_weibo'
    ELSE phase
END
WHERE phase IS NULL OR phase NOT LIKE 'step%';

INSERT IGNORE INTO schema_migrations (version) VALUES ('007_hermes_tool_outputs_phase_step_key');
