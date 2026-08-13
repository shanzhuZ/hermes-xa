-- 大屏 L4 能力节点详情（简介 / 对系统帮助 / 文字示例 / URL 多图 / HBase rowKey 多图）
-- node_id 对齐 ES hermes_xa_agent_node 的 L4 id（cap_*）
-- 含原 020：image_row_keys（与 image_examples 并存）
CREATE TABLE IF NOT EXISTS hermes_agent_l4_detail (
    id               BIGINT        NOT NULL AUTO_INCREMENT COMMENT '自增主键',
    node_id          VARCHAR(128)  NOT NULL COMMENT 'ES L4 节点 id，如 cap_agent_twitter_posts',
    node_name        VARCHAR(128)  NOT NULL COMMENT '节点中文名（冗余，便于核对）',
    parent_agent_id  VARCHAR(128)  NOT NULL COMMENT '所属 L3 agent id',
    intro            TEXT          NOT NULL COMMENT '简介',
    system_help      TEXT          NOT NULL COMMENT '对系统的帮助',
    text_example     TEXT          NOT NULL COMMENT '文字示例',
    image_examples   JSON          NOT NULL COMMENT '图片示例 URL 数组，如 ["url1","url2"]',
    image_row_keys   JSON          NOT NULL COMMENT 'HBase 原图 RowKey 有序数组，如 ["l4:node:01","l4:node:02"]',
    created_at       DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '创建时间',
    updated_at       DATETIME(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '更新时间',

    PRIMARY KEY (id),
    UNIQUE KEY uk_node_id (node_id),
    KEY idx_parent_agent (parent_agent_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='大屏 L4 节点详情展示数据';

INSERT IGNORE INTO schema_migrations (version) VALUES ('019_hermes_agent_l4_detail');

-- 可重复执行：按 node_id 覆盖更新
INSERT INTO hermes_agent_l4_detail
    (node_id, node_name, parent_agent_id, intro, system_help, text_example, image_examples, image_row_keys)
VALUES
-- ========== 账号核查 / 谷歌地图位置核验 ==========
(
    'cap_agent_google_map_verify_geo_recognize',
    '地理位置识别',
    'agent_google_map_verify',
    '从文本、图片或街景线索中识别地理位置实体，输出规范地名、坐标与置信度，支撑后续地图核验。',
    '为账号核查提供「人在何处」的客观锚点，降低仅凭自述简介带来的误判，便于与常住地、活跃城市交叉比对。',
    '输入：简介「西安交大附近常驻」+ 发图水印「钟楼夜景」
输出：
{
  "place": "西安钟楼",
  "city": "西安市",
  "lat": 34.2610,
  "lng": 108.9420,
  "confidence": 0.86
}',
    JSON_ARRAY(
        'https://picsum.photos/seed/geo-recognize-1/800/450',
        'https://picsum.photos/seed/geo-recognize-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_google_map_verify_geo_recognize:01',
        'l4:cap_agent_google_map_verify_geo_recognize:02'
    )),
(
    'cap_agent_google_map_verify_street_view',
    '街景扫描',
    'agent_google_map_verify',
    '按坐标或地点名称拉取街景/全景切片，辅助人工或模型比对现场特征（建筑立面、路牌、植被等）。',
    '让「地点是否真实存在、是否与素材一致」可被可视化核验，提升地理核查的可解释性与演示说服力。',
    '任务：核验坐标 (34.2610, 108.9420) 是否与用户发布的路口照片一致
结果：街景方位 120° 可见钟楼南侧路牌；与素材立面匹配度 0.79',
    JSON_ARRAY(
        'https://picsum.photos/seed/street-view-1/800/450',
        'https://picsum.photos/seed/street-view-2/800/450',
        'https://picsum.photos/seed/street-view-3/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_google_map_verify_street_view:01',
        'l4:cap_agent_google_map_verify_street_view:02',
        'l4:cap_agent_google_map_verify_street_view:03'
    )),
(
    'cap_agent_google_map_verify_place_extract',
    '地点抽取',
    'agent_google_map_verify',
    '从博文、评论、字幕等多源文本中批量抽取地点提及，去重并归一到标准地名体系。',
    '为地图图层、热点聚合与常住地推断提供结构化地点清单，减少人工抄录成本。',
    '原文：「周末从雁塔区赶到未央区办事，晚上回长安区」
抽取：
- 雁塔区
- 未央区
- 长安区
归一：西安市 / 雁塔区、未央区、长安区',
    JSON_ARRAY(
        'https://picsum.photos/seed/place-extract-1/800/450',
        'https://picsum.photos/seed/place-extract-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_google_map_verify_place_extract:01',
        'l4:cap_agent_google_map_verify_place_extract:02'
    )),

-- ========== 账号核查 / 陕西谣言特色库 ==========
(
    'cap_agent_rumor_sx_feature_match',
    '谣言特征匹配',
    'agent_rumor_sx',
    '对照陕西区域谣言特色库，对文本/图文进行特征匹配，输出命中条目、相似度与风险标签。',
    '把「是否像已知谣言」变成可检索、可复现的匹配结果，支撑账号与话题的风险初筛。',
    '样本：「某地今晚要发大水，赶紧转发告知家人」
命中：库条目 R-SX-2023-041「突发灾害恐吓扩散」
相似度：0.91 | 标签：灾害恐吓 / 扩散诱导',
    JSON_ARRAY(
        'https://picsum.photos/seed/rumor-feature-1/800/450',
        'https://picsum.photos/seed/rumor-feature-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_rumor_sx_feature_match:01',
        'l4:cap_agent_rumor_sx_feature_match:02'
    )),
(
    'cap_agent_rumor_sx_topic_agg',
    '区域话题聚合',
    'agent_rumor_sx',
    '按区域与时间窗聚合相关话题簇，展示扩散路径摘要与高频话术模板。',
    '帮助系统从单条内容上升到「区域话题态势」，便于报告中描述舆情热点与关联账号群。',
    '话题簇：#陕北某县矿难谣言
时间窗：近 72h
样本数：128 | 关联账号：37
高频话术：「内部消息」「别信官方」「赶紧转」',
    JSON_ARRAY(
        'https://picsum.photos/seed/rumor-topic-1/800/450',
        'https://picsum.photos/seed/rumor-topic-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_rumor_sx_topic_agg:01',
        'l4:cap_agent_rumor_sx_topic_agg:02'
    )),
(
    'cap_agent_rumor_sx_risk_mark',
    '高风险样本标记',
    'agent_rumor_sx',
    '对匹配或聚合后的样本打高风险标记，沉淀可回放的证据片段与处置建议。',
    '为写报与人工复核提供统一风险分级，避免漏标或重复标注，缩短研判闭环时间。',
    '标记：HIGH
依据：特征命中 + 扩散诱导话术 + 伪造截图痕迹
建议：优先复核发布账号与同源转发链',
    JSON_ARRAY(
        'https://picsum.photos/seed/rumor-risk-1/800/450',
        'https://picsum.photos/seed/rumor-risk-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_rumor_sx_risk_mark:01',
        'l4:cap_agent_rumor_sx_risk_mark:02'
    )),

-- ========== 多平台账号扫描 / Maigret ==========
(
    'cap_agent_maigret_scan_collect',
    '用户名跨平台收集',
    'agent_maigret_scan',
    '以用户名/昵称为种子，在多站点探测同名账号是否存在，汇总可访问主页链接。',
    '快速扩大「一人多号」候选池，为后续采集与身份认定提供入口清单。',
    '种子用户名：li_teacher_xa
命中平台：Twitter / GitHub / Reddit / 即刻
示例链接：https://twitter.com/li_teacher_xa',
    JSON_ARRAY(
        'https://picsum.photos/seed/maigret-collect-1/800/450',
        'https://picsum.photos/seed/maigret-collect-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_maigret_scan_collect:01',
        'l4:cap_agent_maigret_scan_collect:02'
    )),
(
    'cap_agent_maigret_scan_sites',
    '站点命中汇总',
    'agent_maigret_scan',
    '将跨平台探测结果整理为站点命中表：存在性、状态码、头像指纹与备注。',
    '让扫描结果可直接进入展示层与任务树，支撑人工勾选「确认采集范围」。',
    '| 站点 | 存在 | HTTP | 备注 |
| Twitter | 是 | 200 | 头像一致 |
| GitHub | 是 | 200 | 简介含西安 |
| Instagram | 否 | 404 | — |',
    JSON_ARRAY(
        'https://picsum.photos/seed/maigret-sites-1/800/450',
        'https://picsum.photos/seed/maigret-sites-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_maigret_scan_sites:01',
        'l4:cap_agent_maigret_scan_sites:02'
    )),

-- ========== 多平台账号扫描 / 网页检索 ==========
(
    'cap_agent_web_scan_web_search',
    '搜索引擎检索',
    'agent_web_scan',
    '围绕账号线索发起搜索引擎检索，收集相关网页标题、摘要与落地 URL。',
    '补充 Maigret 未覆盖的公开网页线索，发现新闻报道、论坛帖与个人站点。',
    '查询：「李老师 西安」site:edu.cn
Top3：
1. 某高校师资介绍页
2. 讲座新闻稿
3. 个人主页备份',
    JSON_ARRAY(
        'https://picsum.photos/seed/web-search-1/800/450',
        'https://picsum.photos/seed/web-search-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_web_scan_web_search:01',
        'l4:cap_agent_web_scan_web_search:02'
    )),
(
    'cap_agent_web_scan_web_extract',
    '页面正文抽取',
    'agent_web_scan',
    '对检索命中的页面抽取正文、作者信息与关键段落，去除导航与广告噪声。',
    '把网页噪声降为可分析文本流，供观点、地点与身份线索后续模块消费。',
    'URL：https://example.edu.cn/teacher/li
抽取摘要：主要从事网络空间安全教学；联系邮箱 li@example.edu.cn
正文长度：2140 字',
    JSON_ARRAY(
        'https://picsum.photos/seed/web-extract-1/800/450',
        'https://picsum.photos/seed/web-extract-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_web_scan_web_extract:01',
        'l4:cap_agent_web_scan_web_extract:02'
    )),

-- ========== 采集 / Twitter ==========
(
    'cap_agent_twitter_posts',
    '发文采集',
    'agent_twitter',
    '采集目标 Twitter 账号的历史发文（文本、媒体链接、时间、互动数），形成可入库的帖子流。',
    '为文本流/图片流与观点分析提供核心语料，支撑时间线与行为画像。',
    '账号：@li_teacher_xa
采集窗口：近 90 天
帖子数：156 | 含媒体：42
示例：「今天在钟楼附近开会 #西安」',
    JSON_ARRAY(
        'https://picsum.photos/seed/tw-posts-1/800/450',
        'https://picsum.photos/seed/tw-posts-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_twitter_posts:01',
        'l4:cap_agent_twitter_posts:02'
    )),
(
    'cap_agent_twitter_followers',
    '关注与粉丝列表采集',
    'agent_twitter',
    '拉取关注列表与粉丝列表的基础资料（handle、显示名、简介摘要），用于关系初探。',
    '为关系网构图提供一度邻接账号，发现桥接号与社群聚类线索。',
    '目标：@li_teacher_xa
关注：320 | 粉丝：1.2万（采样 Top500）
样例粉丝：@xa_news_bot / @campus_photo',
    JSON_ARRAY(
        'https://picsum.photos/seed/tw-followers-1/800/450',
        'https://picsum.photos/seed/tw-followers-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_twitter_followers:01',
        'l4:cap_agent_twitter_followers:02'
    )),
(
    'cap_agent_twitter_replies',
    '推文回复采集',
    'agent_twitter',
    '采集指定推文线程下的回复与引用，保留对话上下文与互动账号。',
    '补齐「发声—回应」链条，利于观点归纳与涉华倾向在评论区的补充证据。',
    '主帖 ID：1842xxxx
回复数：89 | 去重账号：61
高频回应：「同意」「求出处」「已核实」',
    JSON_ARRAY(
        'https://picsum.photos/seed/tw-replies-1/800/450',
        'https://picsum.photos/seed/tw-replies-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_twitter_replies:01',
        'l4:cap_agent_twitter_replies:02'
    )),

-- ========== 采集 / Youtube ==========
(
    'cap_agent_youtube_channel_list',
    '频道列表采集',
    'agent_youtube',
    '采集频道基础信息与视频列表元数据（标题、发布时间、时长、播放量）。',
    '建立视频侧账号档案入口，为字幕抽取与关键帧分析提供候选清单。',
    '频道：李老师讲安全
视频列表（近 30 条）：
1. 《西安城市安全观察》 12:08  播放 3.2万
2. 《谣言如何扩散》 08:41  播放 1.1万',
    JSON_ARRAY(
        'https://picsum.photos/seed/yt-channel-1/800/450',
        'https://picsum.photos/seed/yt-channel-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_youtube_channel_list:01',
        'l4:cap_agent_youtube_channel_list:02'
    )),
(
    'cap_agent_youtube_subtitle',
    '视频字幕抽取',
    'agent_youtube',
    '抽取视频字幕/自动字幕，按时间码切分为可检索文本段落。',
    '把视频内容转为文本流，接入观点、地点与谣言特征等下游分析。',
    '[00:12] 我们今天聊一下陕西地区常见的谣言话术
[00:48] 这类内容往往带有恐吓和诱导转发
[01:20] 请以官方通报为准',
    JSON_ARRAY(
        'https://picsum.photos/seed/yt-subtitle-1/800/450',
        'https://picsum.photos/seed/yt-subtitle-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_youtube_subtitle:01',
        'l4:cap_agent_youtube_subtitle:02'
    )),
(
    'cap_agent_youtube_profile',
    '主页信息采集',
    'agent_youtube',
    '采集频道主页描述、外链、国家/语言等资料字段，形成账号画像素材。',
    '补齐跨平台身份比对所需的简介与外链线索，与 Twitter/网页结果对齐。',
    '显示名：李老师讲安全
简介：关注城市安全与网络谣言辨析 | 常驻西安
外链：https://example.edu.cn/teacher/li',
    JSON_ARRAY(
        'https://picsum.photos/seed/yt-profile-1/800/450',
        'https://picsum.photos/seed/yt-profile-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_youtube_profile:01',
        'l4:cap_agent_youtube_profile:02'
    )),

-- ========== 报告生产 / 图片流 ==========
(
    'cap_agent_img_flow_multimodal',
    '多模态分析',
    'agent_img_flow',
    '对图片流中的头像、配图、截图做多模态理解，输出场景、物体、文字 OCR 与语义摘要。',
    '让报告可引用「图里看到了什么」，增强视觉证据链，而不仅是文本摘录。',
    '图片类型：街头夜景自拍
OCR：路牌「南大街」
场景标签：城市夜景 / 人群 / 霓虹
摘要：疑似西安南大街夜间街景',
    JSON_ARRAY(
        'https://picsum.photos/seed/img-mm-1/800/450',
        'https://picsum.photos/seed/img-mm-2/800/450',
        'https://picsum.photos/seed/img-mm-3/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_img_flow_multimodal:01',
        'l4:cap_agent_img_flow_multimodal:02',
        'l4:cap_agent_img_flow_multimodal:03'
    )),
(
    'cap_agent_img_flow_img_text_align',
    '图文一致性核验',
    'agent_img_flow',
    '比对配文宣称与图像内容是否一致，识别夸大、移花接木或张冠李戴。',
    '降低虚假图文进入终稿的风险，为谣言与事实核验提供可展示的冲突点。',
    '配文：「此刻洪水已淹没县城主干道」
图像分析：晴天城市干道，无明显积水
结论：不一致（冲突） | 置信度 0.88',
    JSON_ARRAY(
        'https://picsum.photos/seed/img-align-1/800/450',
        'https://picsum.photos/seed/img-align-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_img_flow_img_text_align:01',
        'l4:cap_agent_img_flow_img_text_align:02'
    )),
(
    'cap_agent_img_flow_keyframe',
    '视频关键帧核验',
    'agent_img_flow',
    '从视频中抽取关键帧，检测画面突变、重复素材与可疑剪辑点，并与字幕对齐。',
    '支撑视频证据在报告中的可视化呈现，快速定位「最能说明问题」的画面。',
    '视频时长：08:41
关键帧：00:12 / 03:05 / 07:40
备注：03:05 处出现与配文无关的旧闻画面',
    JSON_ARRAY(
        'https://picsum.photos/seed/keyframe-1/800/450',
        'https://picsum.photos/seed/keyframe-2/800/450',
        'https://picsum.photos/seed/keyframe-3/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_img_flow_keyframe:01',
        'l4:cap_agent_img_flow_keyframe:02',
        'l4:cap_agent_img_flow_keyframe:03'
    )),

-- ========== 报告生产 / 观点与涉华 ==========
(
    'cap_agent_views_cn_views_summary',
    '观点归纳',
    'agent_views_cn',
    '对采集文本进行观点聚类与归纳，输出主要立场簇、代表语句与占比。',
    '帮助写报模块快速形成「舆论观点结构」，避免堆砌原始帖子。',
    '立场簇：
1. 支持官方核查（42%）
2. 质疑信息源（31%）
3. 呼吁勿传谣（27%）
代表句：「先等通报再转发」',
    JSON_ARRAY(
        'https://picsum.photos/seed/views-summary-1/800/450',
        'https://picsum.photos/seed/views-summary-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_views_cn_views_summary:01',
        'l4:cap_agent_views_cn_views_summary:02'
    )),
(
    'cap_agent_views_cn_china_stance',
    '涉华倾向识别',
    'agent_views_cn',
    '识别文本中与涉华议题相关的倾向标签（友好/中性/负面/煽动等）及证据句。',
    '为风险矩阵与报告定调提供结构化标签，便于按倾向筛选样本。',
    '样本：「……故意抹黑相关举措……」
倾向：负面 / 质疑
证据句：同上 | 置信度 0.83',
    JSON_ARRAY(
        'https://picsum.photos/seed/china-stance-1/800/450',
        'https://picsum.photos/seed/china-stance-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_views_cn_china_stance:01',
        'l4:cap_agent_views_cn_china_stance:02'
    )),

-- ========== 报告生产 / 籍贯常住地 ==========
(
    'cap_agent_geo_city_origin_text',
    '基于文本流的籍贯抽取',
    'agent_geo_city',
    '从简介、发文、评论等文本流中抽取籍贯/家乡表述，并归一到行政区划。',
    '丰富人物画像的地理维度，与地图核验、活跃城市结果交叉印证。',
    '文本线索：「老家在陕北绥德」「过年回清涧」
抽取籍贯：陕西省榆林市绥德县（主） / 清涧县（关联）
置信度：0.77',
    JSON_ARRAY(
        'https://picsum.photos/seed/origin-text-1/800/450',
        'https://picsum.photos/seed/origin-text-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_geo_city_origin_text:01',
        'l4:cap_agent_geo_city_origin_text:02'
    )),
(
    'cap_agent_geo_city_reside_mm',
    '基于多模态的常住地抽取',
    'agent_geo_city',
    '结合图片场景、街景匹配与签到类视觉线索，推断常住地/活跃城市。',
    '弥补纯文本自述不足，提升「常住何处」判断在报告中的可信度与可展示性。',
    '视觉线索：多次出现长安区校园地标；夜景街拍匹配南大街
推断常住地：西安市（长安区/雁塔区活跃）
置信度：0.81',
    JSON_ARRAY(
        'https://picsum.photos/seed/reside-mm-1/800/450',
        'https://picsum.photos/seed/reside-mm-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_geo_city_reside_mm:01',
        'l4:cap_agent_geo_city_reside_mm:02'
    )),

-- ========== 报告生产 / 关系网 ==========
(
    'cap_agent_network_viz_evidence',
    '关系证据梳理',
    'agent_network_viz',
    '梳理账号间互动、共同关注、互粉、同文转发等关系证据，形成边列表与证据说明。',
    '为构图提供可追溯边依据，避免「看起来有关」却无法解释的连线。',
    '边：A --转发--> B
证据：2024-11-03 转发原帖并评论「已核实」
边：A --互关--> C
证据：双方关注列表交叉命中',
    JSON_ARRAY(
        'https://picsum.photos/seed/net-evidence-1/800/450',
        'https://picsum.photos/seed/net-evidence-2/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_network_viz_evidence:01',
        'l4:cap_agent_network_viz_evidence:02'
    )),
(
    'cap_agent_network_viz_compose',
    '关系构图',
    'agent_network_viz',
    '基于证据边生成关系网络图（节点、边类型、社区划分），输出可视化与导出数据。',
    '在大屏与报告中直观展示「圈子结构」与桥接账号，强化系统研判表达力。',
    '节点：42 | 边：67
社区：3（校园信息源 / 本地自媒体 / 转发放大器）
桥接账号：@xa_news_bot',
    JSON_ARRAY(
        'https://picsum.photos/seed/net-compose-1/800/450',
        'https://picsum.photos/seed/net-compose-2/800/450',
        'https://picsum.photos/seed/net-compose-3/800/450'
    ),
    JSON_ARRAY(
        'l4:cap_agent_network_viz_compose:01',
        'l4:cap_agent_network_viz_compose:02',
        'l4:cap_agent_network_viz_compose:03'
    ))
ON DUPLICATE KEY UPDATE
    node_name = VALUES(node_name),
    parent_agent_id = VALUES(parent_agent_id),
    intro = VALUES(intro),
    system_help = VALUES(system_help),
    text_example = VALUES(text_example),
    image_examples = VALUES(image_examples),
    image_row_keys = VALUES(image_row_keys),
    updated_at = CURRENT_TIMESTAMP(3);
