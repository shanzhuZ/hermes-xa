-- collect_phase_steps 新增 source_tag（付费 / 开源 / 离线）
-- 执行：
-- mysql -h 127.0.0.1 -P 3306 -u root -p --default-character-set=utf8mb4 hermes-xa < 017_collect_phase_steps_source_tag.sql

USE `hermes-xa`;

ALTER TABLE collect_phase_steps
    ADD COLUMN source_tag VARCHAR(16) NULL COMMENT '付费/开源/离线；壳与纯研判步为空' AFTER title;

INSERT IGNORE INTO schema_migrations (version) VALUES ('017_collect_phase_steps_source_tag');
