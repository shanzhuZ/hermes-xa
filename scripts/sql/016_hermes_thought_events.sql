-- 思考流可回放：仅落库 tool.progress + toolName=_thinking 整句
CREATE TABLE IF NOT EXISTS hermes_thought_events (
    id           BIGINT        NOT NULL AUTO_INCREMENT COMMENT '主键',
    task_id      VARCHAR(64)   NOT NULL COMMENT '所属任务ID',
    seq          INT           NOT NULL COMMENT '与 SSE 同一单调序号',
    event_type   VARCHAR(64)   NOT NULL DEFAULT 'tool.progress' COMMENT '事件类型',
    tool_name    VARCHAR(128)  NOT NULL DEFAULT '_thinking' COMMENT '工具名，思考整句为 _thinking',
    content      VARCHAR(2048) NOT NULL COMMENT '整句进度文案（截断）',
    created_at   DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    PRIMARY KEY (id),
    UNIQUE KEY uk_task_seq (task_id, seq),
    KEY idx_task_seq (task_id, seq),
    CONSTRAINT fk_hermes_thought_events_task
        FOREIGN KEY (task_id) REFERENCES hermes_tasks(task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='思考流精简事件（_thinking 整句，供历史/刷新回放）';

INSERT IGNORE INTO schema_migrations (version) VALUES ('016_hermes_thought_events');
