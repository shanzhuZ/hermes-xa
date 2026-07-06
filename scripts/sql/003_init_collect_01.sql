-- 01 账号信息采集 — 9 张业务表（表与字段均含中文注释）
-- 执行：mysql -h 127.0.0.1 -P 3306 -u root -p < 003_init_collect_01.sql

CREATE DATABASE IF NOT EXISTS `hermes-xa`
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci
  COMMENT '账号智能分析平台业务库';

USE `hermes-xa`;

-- ========== 1. 总流程状态表 ==========
CREATE TABLE IF NOT EXISTS hermes_tasks (
    task_id         VARCHAR(64)   NOT NULL PRIMARY KEY COMMENT '任务唯一ID，Java 生成 UUID',
    task_type       VARCHAR(32)   NOT NULL DEFAULT 'account_collect' COMMENT '任务类型，01 固定为 account_collect（账号信息采集）',
    session_id      VARCHAR(64)   NULL     COMMENT 'Hermes 会话 ID，对接 Agent 时使用',
    status          VARCHAR(16)   NOT NULL DEFAULT 'pending' COMMENT '任务状态：pending 待执行 / running 执行中 / completed 已完成 / failed 失败',
    current_phase   VARCHAR(32)   NULL     COMMENT '当前执行阶段：resolve_seed 解析种子 / cross_platform 跨平台检索 / verify 真实性核验 / stream_gen 特征流生成 / stream_validate 流校验 / account_finalize 账号收敛 / collect 分平台采集 / done 结束',
    cross_platform  TINYINT(1)    NULL     COMMENT '是否跨平台采集：1 是 / 0 否，由 Java 根据前端点选写入',
    seed_json       JSON          NULL     COMMENT '种子账号 JSON：platform 平台、account_hint 用户输入、account_id 解析后平台ID 等',
    subject_json    JSON          NULL     COMMENT '任务扩展参数 JSON，备用',
    error_message   TEXT          NULL     COMMENT '失败时的错误信息',
    started_at      DATETIME(3)   NULL     COMMENT '任务开始执行时间',
    finished_at     DATETIME(3)   NULL     COMMENT '任务结束时间',
    created_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '记录创建时间',
    updated_at      DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '记录最后更新时间',
    KEY idx_status (status),
    KEY idx_phase (current_phase),
    KEY idx_session (session_id),
    KEY idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='总流程状态表：一次账号采集任务的全局状态与阶段';

-- ========== 2. 用户对话表 ==========
CREATE TABLE IF NOT EXISTS hermes_user_dialogues (
    id          BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id     VARCHAR(64)   NOT NULL COMMENT '关联任务 ID',
    session_id  VARCHAR(64)   NULL     COMMENT 'Hermes 会话 ID',
    role        VARCHAR(16)   NOT NULL COMMENT '消息角色：user 用户 / assistant 助手 / system 系统',
    content     TEXT          NOT NULL COMMENT '消息正文，含用户一句话或系统回复',
    msg_type    VARCHAR(32)   NOT NULL DEFAULT 'user_input' COMMENT '消息类型：user_input 用户输入 / assistant_reply 助手回复 / phase_progress 阶段进度 / summary 结果摘要',
    created_at  DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '消息时间',
    KEY idx_task (task_id),
    KEY idx_task_time (task_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户对话表：记录用户一句话及系统关键回复';

-- ========== 3. MCP 工具调用表 ==========
CREATE TABLE IF NOT EXISTS hermes_tool_outputs (
    id            BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id       VARCHAR(64)   NOT NULL COMMENT '关联任务 ID',
    phase         VARCHAR(32)   NULL     COMMENT '调用发生时任务所处阶段，同 hermes_tasks.current_phase',
    mcp_server    VARCHAR(64)   NULL     COMMENT 'MCP 服务名，如 weibo、twitter、maigret',
    tool_name     VARCHAR(128)  NOT NULL COMMENT '工具全名，如 mcp_weibo_get_profile',
    tool_args     JSON          NULL     COMMENT '工具入参 JSON',
    tool_output   LONGTEXT      NOT NULL COMMENT 'MCP 返回的完整原始 JSON，审计真源，不截断',
    tool_call_id  VARCHAR(128)  NOT NULL COMMENT 'Hermes 工具调用唯一 ID，防重复入库',
    duration_ms   INT           NULL     COMMENT '调用耗时（毫秒）',
    status        VARCHAR(16)   NULL     COMMENT '调用结果：success 成功 / error 失败',
    executed_at   DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '工具执行完成时间',
    UNIQUE KEY uk_tool_call (tool_call_id),
    KEY idx_task (task_id),
    KEY idx_tool (tool_name),
    KEY idx_phase (task_id, phase)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='MCP 工具调用表：每次 MCP 调用的完整原始输出';

-- ========== 4. 跨平台候选（阶段 3） ==========
CREATE TABLE IF NOT EXISTS cross_platform_candidates (
    id               BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id          VARCHAR(64)   NOT NULL COMMENT '关联任务 ID',
    platform         VARCHAR(32)   NOT NULL COMMENT '候选账号所在平台，如 weibo、bilibili、twitter',
    account_id       VARCHAR(128)  NOT NULL COMMENT '平台内账号唯一 ID',
    account_handle   VARCHAR(256)  NULL     COMMENT '账号展示名或 @handle',
    confidence       DECIMAL(5,4)  NULL     COMMENT '匹配置信度 0～1',
    evidence_json    JSON          NULL     COMMENT '匹配依据 JSON，如昵称相同、简介含相同链接',
    match_strategy   VARCHAR(64)   NULL     COMMENT '匹配策略：maigret / nickname 昵称 / bio_link 简介链接 等',
    status           VARCHAR(16)   NOT NULL DEFAULT 'candidate' COMMENT '候选状态：candidate 候选 / excluded 已排除 / weak 弱线索',
    raw_json         JSON          NULL     COMMENT '平台返回的原始候选对象 JSON',
    tool_output_id   BIGINT        NULL     COMMENT '关联 hermes_tool_outputs.id，可追溯来源调用',
    created_at       DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间',
    KEY idx_task (task_id),
    KEY idx_task_platform (task_id, platform)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='跨平台候选账号表：阶段3 检索到的疑似同人账号';

-- ========== 5. 特征流（阶段 5～6） ==========
CREATE TABLE IF NOT EXISTS collect_identity_streams (
    stream_id          VARCHAR(64)   NOT NULL PRIMARY KEY COMMENT '特征流唯一 ID',
    task_id            VARCHAR(64)   NOT NULL COMMENT '关联任务 ID',
    stream_type        VARCHAR(16)   NOT NULL COMMENT '流类型：text 文本流 / image 图片流',
    source_platform    VARCHAR(32)   NULL     COMMENT '来源平台',
    source_account_id  VARCHAR(128)  NULL     COMMENT '来源账号 ID',
    source_field       VARCHAR(64)   NULL     COMMENT '来源字段：bio 简介 / avatar 头像 / display_name 昵称 等',
    payload_text       TEXT          NULL     COMMENT '文本流内容',
    payload_url        VARCHAR(512)  NULL     COMMENT '图片流 URL（头像、主页图等）',
    validation_status  VARCHAR(16)   NOT NULL DEFAULT 'pending' COMMENT '校验状态：pending 待校验 / pass 通过 / fail 失败 / low_quality 质量过低',
    validation_detail  TEXT          NULL     COMMENT '校验说明或失败原因',
    extra_json         JSON          NULL     COMMENT '扩展字段 JSON',
    created_at         DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
    updated_at         DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',
    KEY idx_task (task_id),
    KEY idx_task_type (task_id, stream_type),
    KEY idx_validation (task_id, validation_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='特征流表：从主页/简介/头像生成的文本流与图片流及校验结果';

-- ========== 6. 已校验账号清单（阶段 7） ==========
CREATE TABLE IF NOT EXISTS collect_validated_accounts (
    id               BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id          VARCHAR(64)   NOT NULL COMMENT '关联任务 ID',
    platform         VARCHAR(32)   NOT NULL COMMENT '平台标识',
    account_id       VARCHAR(128)  NOT NULL COMMENT '平台内账号 ID',
    account_handle   VARCHAR(256)  NULL     COMMENT '账号 @名或昵称',
    confidence       DECIMAL(5,4)  NULL     COMMENT '综合置信度 0～1',
    verdict          VARCHAR(32)   NOT NULL DEFAULT 'validated' COMMENT '判定结果：validated 通过 / rejected 拒绝 / insufficient 数据不足',
    stream_ids_json  JSON          NULL     COMMENT '支撑判定的特征流 ID 列表 JSON',
    is_seed          TINYINT(1)    NOT NULL DEFAULT 0 COMMENT '是否种子账号：1 是用户最初指定的账号 / 0 否',
    extra_json       JSON          NULL     COMMENT '扩展字段 JSON',
    created_at       DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间',
    UNIQUE KEY uk_task_account (task_id, platform, account_id),
    KEY idx_task_verdict (task_id, verdict)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='已校验账号清单：阶段7 收敛后进入分平台采集的账号列表';

-- ========== 7. 人物信息表 ==========
CREATE TABLE IF NOT EXISTS collect_profiles (
    id                   BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id              VARCHAR(64)   NOT NULL COMMENT '关联任务 ID',
    platform             VARCHAR(32)   NOT NULL COMMENT '平台标识',
    account_id           VARCHAR(128)  NOT NULL COMMENT '平台内账号 ID',
    account_handle       VARCHAR(256)  NULL     COMMENT '账号 @名或自定义 ID',
    display_name         VARCHAR(256)  NULL     COMMENT '显示昵称',
    bio                  TEXT          NULL     COMMENT '个人简介',
    avatar_url           VARCHAR(512)  NULL     COMMENT '头像 URL',
    profile_url          VARCHAR(512)  NULL     COMMENT '主页链接',
    follower_count       BIGINT        NULL     COMMENT '粉丝数',
    following_count      BIGINT        NULL     COMMENT '关注数',
    content_count        BIGINT        NULL     COMMENT '内容总数（博文/视频等，语义因平台而异）',
    verified             TINYINT(1)    NULL     COMMENT '是否官方认证：1 是 / 0 否',
    visibility           VARCHAR(16)   NULL     COMMENT '可见性：public 公开 / partial 部分可见 / unknown 未知',
    collect_status       VARCHAR(16)   NULL     COMMENT '采集状态：success 成功 / empty 无数据 / partial 部分 / error 失败',
    tool_output_id       BIGINT        NULL     COMMENT '关联 hermes_tool_outputs.id',
    raw_json             JSON          NULL     COMMENT '平台原始 profile 对象 JSON，完整保留',
    collect_options_json JSON          NULL     COMMENT '本次采集条件 JSON（时间范围、条数上限等）',
    metrics_json         JSON          NULL     COMMENT '平台特有指标 JSON，如 B站投币数',
    extra_json           JSON          NULL     COMMENT '暂未提升为独立列的扩展字段',
    collected_at         DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '资料快照采集时间',
    UNIQUE KEY uk_task_account (task_id, platform, account_id),
    KEY idx_account (platform, account_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='人物信息表：各平台账号资料快照（统一常用列 + 原始JSON）';

-- ========== 8. 发文信息表 ==========
CREATE TABLE IF NOT EXISTS collect_posts (
    id                BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id           VARCHAR(64)   NOT NULL COMMENT '关联任务 ID',
    platform          VARCHAR(32)   NOT NULL COMMENT '平台标识',
    account_id        VARCHAR(128)  NOT NULL COMMENT '发文作者账号 ID',
    content_id        VARCHAR(128)  NOT NULL COMMENT '平台内内容唯一 ID（博文ID/视频ID等）',
    content_type      VARCHAR(32)   NOT NULL DEFAULT 'post' COMMENT '内容类型：post 原创帖 / repost 转发 / video 视频 / article 长文 / comment 评论',
    parent_content_id VARCHAR(128)  NULL     COMMENT '父内容 ID，转发源帖或被评论博文',
    title             VARCHAR(512)  NULL     COMMENT '标题（视频、公众号等）',
    content_text      TEXT          NULL     COMMENT '正文文本，过长时可截断',
    content_url       VARCHAR(512)  NULL     COMMENT '内容可访问链接',
    published_at      DATETIME(3)   NULL     COMMENT '发布时间',
    view_count        BIGINT        NULL     COMMENT '浏览量或播放量，无则 NULL',
    like_count        BIGINT        NULL     COMMENT '点赞数',
    comment_count     BIGINT        NULL     COMMENT '评论数',
    repost_count      BIGINT        NULL     COMMENT '转发或分享数',
    media_json        JSON          NULL     COMMENT '媒体列表 JSON：[{type, url, thumb}]',
    metrics_json      JSON          NULL     COMMENT '平台扩展互动指标 JSON',
    matched_keywords  JSON          NULL     COMMENT '命中关键词列表 JSON',
    text_truncated    TINYINT(1)    NOT NULL DEFAULT 0 COMMENT '正文是否已截断：1 是 / 0 否',
    tool_output_id    BIGINT        NULL     COMMENT '关联 hermes_tool_outputs.id',
    raw_json          JSON          NULL     COMMENT '平台原始内容对象 JSON',
    extra_json        JSON          NULL     COMMENT '扩展字段 JSON',
    created_at        DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '入库时间',
    UNIQUE KEY uk_task_content (task_id, platform, content_id),
    KEY idx_task (task_id),
    KEY idx_account (platform, account_id),
    KEY idx_published (task_id, published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='发文信息表：博文/视频/转发等内容明细（统一常用列 + 原始JSON）';

-- ========== 9. 任务数据摘要表 ==========
CREATE TABLE IF NOT EXISTS collect_task_summaries (
    id                      BIGINT        NOT NULL AUTO_INCREMENT PRIMARY KEY COMMENT '自增主键',
    task_id                 VARCHAR(64)   NOT NULL COMMENT '关联任务 ID，一对一',
    title                   VARCHAR(256)  NULL     COMMENT '摘要标题，如「Twitter 李老师 · 跨平台采集」',
    validated_account_count INT           NULL DEFAULT 0 COMMENT '通过校验的账号数量',
    profile_count           INT           NULL DEFAULT 0 COMMENT '采集到的人物资料条数',
    post_count              INT           NULL DEFAULT 0 COMMENT '采集到的发文条数',
    platforms_json          JSON          NULL     COMMENT '涉及平台列表 JSON，如 ["twitter","bilibili"]',
    summary_text            TEXT          NULL     COMMENT '给人阅读的简短文字摘要',
    summary_json            JSON          NULL     COMMENT '结构化摘要 JSON，供报告或前端使用',
    generated_at            DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '摘要生成时间',
    UNIQUE KEY uk_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='任务数据摘要表：一次采集完成后的统计与摘要';

-- 迁移版本
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     VARCHAR(32)  NOT NULL PRIMARY KEY COMMENT '迁移脚本版本号',
    applied_at  DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '执行时间'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='SQL 迁移版本记录表';

INSERT IGNORE INTO schema_migrations (version) VALUES ('003_init_collect_01');
