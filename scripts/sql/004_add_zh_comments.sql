-- 为已存在的 01 九表补充中文注释（库 hermes-xa）
USE `hermes-xa`;

ALTER TABLE hermes_tasks COMMENT='总流程状态表：一次账号采集任务的全局状态与阶段';
ALTER TABLE hermes_tasks
    MODIFY COLUMN task_id VARCHAR(64) NOT NULL COMMENT '任务唯一ID，Java 生成 UUID',
    MODIFY COLUMN task_type VARCHAR(32) NOT NULL DEFAULT 'account_collect' COMMENT '任务类型，01 固定为 account_collect（账号信息采集）',
    MODIFY COLUMN session_id VARCHAR(64) NULL COMMENT 'Hermes 会话 ID，对接 Agent 时使用',
    MODIFY COLUMN status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '任务状态：pending 待执行 / running 执行中 / completed 已完成 / failed 失败',
    MODIFY COLUMN current_phase VARCHAR(32) NULL COMMENT '当前执行阶段：resolve_seed 解析种子 / cross_platform 跨平台检索 / verify 真实性核验 / stream_gen 特征流生成 / stream_validate 流校验 / account_finalize 账号收敛 / collect 分平台采集 / done 结束',
    MODIFY COLUMN cross_platform TINYINT(1) NULL COMMENT '是否跨平台采集：1 是 / 0 否，由 Java 根据前端点选写入',
    MODIFY COLUMN seed_json JSON NULL COMMENT '种子账号 JSON：platform 平台、account_hint 用户输入、account_id 解析后平台ID 等',
    MODIFY COLUMN subject_json JSON NULL COMMENT '任务扩展参数 JSON，备用',
    MODIFY COLUMN error_message TEXT NULL COMMENT '失败时的错误信息',
    MODIFY COLUMN started_at DATETIME(3) NULL COMMENT '任务开始执行时间',
    MODIFY COLUMN finished_at DATETIME(3) NULL COMMENT '任务结束时间',
    MODIFY COLUMN created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录创建时间',
    MODIFY COLUMN updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '记录最后更新时间';

ALTER TABLE hermes_user_dialogues COMMENT='用户对话表：记录用户一句话及系统关键回复';
ALTER TABLE hermes_user_dialogues
    MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    MODIFY COLUMN task_id VARCHAR(64) NOT NULL COMMENT '关联任务 ID',
    MODIFY COLUMN session_id VARCHAR(64) NULL COMMENT 'Hermes 会话 ID',
    MODIFY COLUMN role VARCHAR(16) NOT NULL COMMENT '消息角色：user 用户 / assistant 助手 / system 系统',
    MODIFY COLUMN content TEXT NOT NULL COMMENT '消息正文，含用户一句话或系统回复',
    MODIFY COLUMN msg_type VARCHAR(32) NOT NULL DEFAULT 'user_input' COMMENT '消息类型：user_input 用户输入 / assistant_reply 助手回复 / phase_progress 阶段进度 / summary 结果摘要',
    MODIFY COLUMN created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '消息时间';

ALTER TABLE hermes_tool_outputs COMMENT='MCP 工具调用表：每次 MCP 调用的完整原始输出';
ALTER TABLE hermes_tool_outputs
    MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    MODIFY COLUMN task_id VARCHAR(64) NOT NULL COMMENT '关联任务 ID',
    MODIFY COLUMN phase VARCHAR(32) NULL COMMENT '调用发生时任务所处阶段，同 hermes_tasks.current_phase',
    MODIFY COLUMN mcp_server VARCHAR(64) NULL COMMENT 'MCP 服务名，如 weibo、twitter、maigret',
    MODIFY COLUMN tool_name VARCHAR(128) NOT NULL COMMENT '工具全名，如 mcp_weibo_get_profile',
    MODIFY COLUMN tool_args JSON NULL COMMENT '工具入参 JSON',
    MODIFY COLUMN tool_output LONGTEXT NOT NULL COMMENT 'MCP 返回的完整原始 JSON，审计真源，不截断',
    MODIFY COLUMN tool_call_id VARCHAR(128) NOT NULL COMMENT 'Hermes 工具调用唯一 ID，防重复入库',
    MODIFY COLUMN duration_ms INT NULL COMMENT '调用耗时（毫秒）',
    MODIFY COLUMN status VARCHAR(16) NULL COMMENT '调用结果：success 成功 / error 失败',
    MODIFY COLUMN executed_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '工具执行完成时间';

ALTER TABLE cross_platform_candidates COMMENT='跨平台候选账号表：阶段3 检索到的疑似同人账号';
ALTER TABLE cross_platform_candidates
    MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    MODIFY COLUMN task_id VARCHAR(64) NOT NULL COMMENT '关联任务 ID',
    MODIFY COLUMN platform VARCHAR(32) NOT NULL COMMENT '候选账号所在平台，如 weibo、bilibili、twitter',
    MODIFY COLUMN account_id VARCHAR(128) NOT NULL COMMENT '平台内账号唯一 ID',
    MODIFY COLUMN account_handle VARCHAR(256) NULL COMMENT '账号展示名或 @handle',
    MODIFY COLUMN confidence DECIMAL(5,4) NULL COMMENT '匹配置信度 0～1',
    MODIFY COLUMN evidence_json JSON NULL COMMENT '匹配依据 JSON，如昵称相同、简介含相同链接',
    MODIFY COLUMN match_strategy VARCHAR(64) NULL COMMENT '匹配策略：maigret / nickname 昵称 / bio_link 简介链接 等',
    MODIFY COLUMN status VARCHAR(16) NOT NULL DEFAULT 'candidate' COMMENT '候选状态：candidate 候选 / excluded 已排除 / weak 弱线索',
    MODIFY COLUMN raw_json JSON NULL COMMENT '平台返回的原始候选对象 JSON',
    MODIFY COLUMN tool_output_id BIGINT NULL COMMENT '关联 hermes_tool_outputs.id，可追溯来源调用',
    MODIFY COLUMN created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间';

ALTER TABLE collect_identity_streams COMMENT='特征流表：从主页/简介/头像生成的文本流与图片流及校验结果';
ALTER TABLE collect_identity_streams
    MODIFY COLUMN stream_id VARCHAR(64) NOT NULL COMMENT '特征流唯一 ID',
    MODIFY COLUMN task_id VARCHAR(64) NOT NULL COMMENT '关联任务 ID',
    MODIFY COLUMN stream_type VARCHAR(16) NOT NULL COMMENT '流类型：text 文本流 / image 图片流',
    MODIFY COLUMN source_platform VARCHAR(32) NULL COMMENT '来源平台',
    MODIFY COLUMN source_account_id VARCHAR(128) NULL COMMENT '来源账号 ID',
    MODIFY COLUMN source_field VARCHAR(64) NULL COMMENT '来源字段：bio 简介 / avatar 头像 / display_name 昵称 等',
    MODIFY COLUMN payload_text TEXT NULL COMMENT '文本流内容',
    MODIFY COLUMN payload_url VARCHAR(512) NULL COMMENT '图片流 URL（头像、主页图等）',
    MODIFY COLUMN validation_status VARCHAR(16) NOT NULL DEFAULT 'pending' COMMENT '校验状态：pending 待校验 / pass 通过 / fail 失败 / low_quality 质量过低',
    MODIFY COLUMN validation_detail TEXT NULL COMMENT '校验说明或失败原因',
    MODIFY COLUMN extra_json JSON NULL COMMENT '扩展字段 JSON',
    MODIFY COLUMN created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
    MODIFY COLUMN updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间';

ALTER TABLE collect_validated_accounts COMMENT='已校验账号清单：阶段7 收敛后进入分平台采集的账号列表';
ALTER TABLE collect_validated_accounts
    MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    MODIFY COLUMN task_id VARCHAR(64) NOT NULL COMMENT '关联任务 ID',
    MODIFY COLUMN platform VARCHAR(32) NOT NULL COMMENT '平台标识',
    MODIFY COLUMN account_id VARCHAR(128) NOT NULL COMMENT '平台内账号 ID',
    MODIFY COLUMN account_handle VARCHAR(256) NULL COMMENT '账号 @名或昵称',
    MODIFY COLUMN confidence DECIMAL(5,4) NULL COMMENT '综合置信度 0～1',
    MODIFY COLUMN verdict VARCHAR(32) NOT NULL DEFAULT 'validated' COMMENT '判定结果：validated 通过 / rejected 拒绝 / insufficient 数据不足',
    MODIFY COLUMN stream_ids_json JSON NULL COMMENT '支撑判定的特征流 ID 列表 JSON',
    MODIFY COLUMN is_seed TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否种子账号：1 是用户最初指定的账号 / 0 否',
    MODIFY COLUMN extra_json JSON NULL COMMENT '扩展字段 JSON',
    MODIFY COLUMN created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间';

ALTER TABLE collect_profiles COMMENT='人物信息表：各平台账号资料快照（统一常用列 + 原始JSON）';
ALTER TABLE collect_profiles
    MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    MODIFY COLUMN task_id VARCHAR(64) NOT NULL COMMENT '关联任务 ID',
    MODIFY COLUMN platform VARCHAR(32) NOT NULL COMMENT '平台标识',
    MODIFY COLUMN account_id VARCHAR(128) NOT NULL COMMENT '平台内账号 ID',
    MODIFY COLUMN account_handle VARCHAR(256) NULL COMMENT '账号 @名或自定义 ID',
    MODIFY COLUMN display_name VARCHAR(256) NULL COMMENT '显示昵称',
    MODIFY COLUMN bio TEXT NULL COMMENT '个人简介',
    MODIFY COLUMN avatar_url VARCHAR(512) NULL COMMENT '头像 URL',
    MODIFY COLUMN profile_url VARCHAR(512) NULL COMMENT '主页链接',
    MODIFY COLUMN follower_count BIGINT NULL COMMENT '粉丝数',
    MODIFY COLUMN following_count BIGINT NULL COMMENT '关注数',
    MODIFY COLUMN content_count BIGINT NULL COMMENT '内容总数（博文/视频等，语义因平台而异）',
    MODIFY COLUMN verified TINYINT(1) NULL COMMENT '是否官方认证：1 是 / 0 否',
    MODIFY COLUMN visibility VARCHAR(16) NULL COMMENT '可见性：public 公开 / partial 部分可见 / unknown 未知',
    MODIFY COLUMN collect_status VARCHAR(16) NULL COMMENT '采集状态：success 成功 / empty 无数据 / partial 部分 / error 失败',
    MODIFY COLUMN tool_output_id BIGINT NULL COMMENT '关联 hermes_tool_outputs.id',
    MODIFY COLUMN raw_json JSON NULL COMMENT '平台原始 profile 对象 JSON，完整保留',
    MODIFY COLUMN collect_options_json JSON NULL COMMENT '本次采集条件 JSON（时间范围、条数上限等）',
    MODIFY COLUMN metrics_json JSON NULL COMMENT '平台特有指标 JSON，如 B站投币数',
    MODIFY COLUMN extra_json JSON NULL COMMENT '暂未提升为独立列的扩展字段',
    MODIFY COLUMN collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '资料快照采集时间';

ALTER TABLE collect_posts COMMENT='发文信息表：博文/视频/转发等内容明细（统一常用列 + 原始JSON）';
ALTER TABLE collect_posts
    MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    MODIFY COLUMN task_id VARCHAR(64) NOT NULL COMMENT '关联任务 ID',
    MODIFY COLUMN platform VARCHAR(32) NOT NULL COMMENT '平台标识',
    MODIFY COLUMN account_id VARCHAR(128) NOT NULL COMMENT '发文作者账号 ID',
    MODIFY COLUMN content_id VARCHAR(128) NOT NULL COMMENT '平台内内容唯一 ID（博文ID/视频ID等）',
    MODIFY COLUMN content_type VARCHAR(32) NOT NULL DEFAULT 'post' COMMENT '内容类型：post 原创帖 / repost 转发 / video 视频 / article 长文 / comment 评论',
    MODIFY COLUMN parent_content_id VARCHAR(128) NULL COMMENT '父内容 ID，转发源帖或被评论博文',
    MODIFY COLUMN title VARCHAR(512) NULL COMMENT '标题（视频、公众号等）',
    MODIFY COLUMN content_text TEXT NULL COMMENT '正文文本，过长时可截断',
    MODIFY COLUMN content_url VARCHAR(512) NULL COMMENT '内容可访问链接',
    MODIFY COLUMN published_at DATETIME(3) NULL COMMENT '发布时间',
    MODIFY COLUMN view_count BIGINT NULL COMMENT '浏览量或播放量，无则 NULL',
    MODIFY COLUMN like_count BIGINT NULL COMMENT '点赞数',
    MODIFY COLUMN comment_count BIGINT NULL COMMENT '评论数',
    MODIFY COLUMN repost_count BIGINT NULL COMMENT '转发或分享数',
    MODIFY COLUMN media_json JSON NULL COMMENT '媒体列表 JSON：[{type, url, thumb}]',
    MODIFY COLUMN metrics_json JSON NULL COMMENT '平台扩展互动指标 JSON',
    MODIFY COLUMN matched_keywords JSON NULL COMMENT '命中关键词列表 JSON',
    MODIFY COLUMN text_truncated TINYINT(1) NOT NULL DEFAULT 0 COMMENT '正文是否已截断：1 是 / 0 否',
    MODIFY COLUMN tool_output_id BIGINT NULL COMMENT '关联 hermes_tool_outputs.id',
    MODIFY COLUMN raw_json JSON NULL COMMENT '平台原始内容对象 JSON',
    MODIFY COLUMN extra_json JSON NULL COMMENT '扩展字段 JSON',
    MODIFY COLUMN created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间';

ALTER TABLE collect_task_summaries COMMENT='任务数据摘要表：一次采集完成后的统计与摘要';
ALTER TABLE collect_task_summaries
    MODIFY COLUMN id BIGINT NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    MODIFY COLUMN task_id VARCHAR(64) NOT NULL COMMENT '关联任务 ID，一对一',
    MODIFY COLUMN title VARCHAR(256) NULL COMMENT '摘要标题，如「Twitter 李老师 · 跨平台采集」',
    MODIFY COLUMN validated_account_count INT NULL DEFAULT 0 COMMENT '通过校验的账号数量',
    MODIFY COLUMN profile_count INT NULL DEFAULT 0 COMMENT '采集到的人物资料条数',
    MODIFY COLUMN post_count INT NULL DEFAULT 0 COMMENT '采集到的发文条数',
    MODIFY COLUMN platforms_json JSON NULL COMMENT '涉及平台列表 JSON，如 ["twitter","bilibili"]',
    MODIFY COLUMN summary_text TEXT NULL COMMENT '给人阅读的简短文字摘要',
    MODIFY COLUMN summary_json JSON NULL COMMENT '结构化摘要 JSON，供报告或前端使用',
    MODIFY COLUMN generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '摘要生成时间';

ALTER TABLE schema_migrations COMMENT='SQL 迁移版本记录表';
ALTER TABLE schema_migrations
    MODIFY COLUMN version VARCHAR(32) NOT NULL COMMENT '迁移脚本版本号',
    MODIFY COLUMN applied_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '执行时间';

INSERT IGNORE INTO schema_migrations (version) VALUES ('004_add_zh_comments');
