-- source_tag 改为 JSON 列表文本，如 ["开源"] / ["自研","离线"]
-- mysql -h 127.0.0.1 -P 3306 -u root -p --default-character-set=utf8mb4 hermes-xa < 018_collect_phase_steps_source_tag_list.sql

USE `hermes-xa`;

ALTER TABLE collect_phase_steps
    MODIFY COLUMN source_tag VARCHAR(64) NULL COMMENT 'JSON列表：付费/开源/离线/自研；壳为空';

-- 旧单字符串规范化为 JSON 数组（已是 [...] 的跳过）
UPDATE collect_phase_steps
SET source_tag = CONCAT('["', source_tag, '"]')
WHERE source_tag IS NOT NULL
  AND source_tag != ''
  AND source_tag NOT LIKE '[%';

INSERT IGNORE INTO schema_migrations (version) VALUES ('018_collect_phase_steps_source_tag_list');
