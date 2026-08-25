-- 成报主题标签（仅 msg_type=summary 行使用）
-- 执行：mysql -h 127.0.0.1 -P 3306 -u root -p --default-character-set=utf8mb4 hermes-xa < 021_hermes_user_dialogues_report_tags.sql

ALTER TABLE hermes_user_dialogues
    ADD COLUMN report_tags JSON NULL
        COMMENT '成报主题标签 JSON 数组，如 ["实名","家庭","涉华"]，仅 summary 行'
        AFTER msg_type;

INSERT IGNORE INTO schema_migrations (version) VALUES ('021_hermes_user_dialogues_report_tags');
