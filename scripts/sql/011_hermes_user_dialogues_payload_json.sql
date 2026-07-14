-- 用户对话表增加前端原始请求 JSON，便于历史对话回显
ALTER TABLE hermes_user_dialogues
    ADD COLUMN payload_json JSON NULL COMMENT '前端传入的原始请求 JSON（原样存储）' AFTER content;

INSERT IGNORE INTO schema_migrations (version) VALUES ('011_hermes_user_dialogues_payload_json');
