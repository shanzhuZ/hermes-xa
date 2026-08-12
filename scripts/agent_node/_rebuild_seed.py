# -*- coding: utf-8 -*-
"""重建 agent_node_seed.json：L3 补齐到大类角标目标数，同步扩 L4，回写 badgeCount/all_count。

角标语义（改后）：
  - L2 badgeCount = 直接 L3 数（与 getChildren 条数一致）
  - L3 badgeCount = 直接 L4 数
  - all_count = 全部子孙节点数（不含自身）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OUT = Path(__file__).resolve().parent / "agent_node_seed.json"

# 各大类目标 L3 数 = 原角标数（用户要求：直接子节点补到角标）
TARGET_L3: Dict[str, int] = {
    "cat_collect": 77,
    "cat_scan": 47,
    "cat_verify": 42,
    "cat_report": 54,
}

def node(
    id_: str,
    parent_id: str,
    name: str,
    level: int,
    type_: str,
    *,
    description: str = "",
    status: str = "online",
    version: str = "v1.0",
    creator: str = "ops",
    create_time: str = "2026-07-03 12:00:00",
    show: bool = True,
    weight: int = 50,
    display_count: int = 3,
    account_count: int = 0,
    platform_count: int = 1,
    last_active: str = "5 分钟前",
) -> Dict[str, Any]:
    return {
        "id": id_,
        "parentId": parent_id,
        "name": name,
        "level": level,
        "type": type_,
        "description": description or name,
        "status": status,
        "version": version,
        "creator": creator,
        "createTime": create_time,
        "badgeCount": 0,
        "allCount": 0,
        "displayConfig": {
            "show": show,
            "weight": weight,
            "displayCount": display_count,
        },
        "stats": {
            "childCount": 0,
            "accountCount": account_count,
            "platformCount": platform_count,
            "lastActive": last_active,
        },
    }


def caps(
    parent: str,
    items: List[tuple],
    *,
    base_weight: int = 100,
) -> List[Dict[str, Any]]:
    """items: (suffix, name, account_count, show?)"""
    out = []
    for i, it in enumerate(items):
        suffix, name, acc = it[0], it[1], it[2]
        show = it[3] if len(it) > 3 else True
        out.append(
            node(
                f"cap_{parent}_{suffix}",
                parent,
                name,
                4,
                "capability",
                description=f"{name}（假数据）",
                show=show,
                weight=max(10, base_weight - i * 10),
                display_count=0,
                account_count=acc,
                platform_count=1,
                last_active=f"{(i % 20) + 1} 分钟前",
            )
        )
    return out


def agent(
    id_: str,
    parent: str,
    name: str,
    *,
    description: str = "",
    show: bool = True,
    weight: int = 50,
    display_count: int = 3,
    platform_count: int = 1,
    last_active: str = "3 分钟前",
    version: str = "v1.0",
) -> Dict[str, Any]:
    return node(
        id_,
        parent,
        name,
        3,
        "agent",
        description=description or name,
        show=show,
        weight=weight,
        display_count=display_count,
        account_count=0,
        platform_count=platform_count,
        last_active=last_active,
        version=version,
        creator="ops",
        create_time="2026-07-02 10:00:00",
    )


def build() -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []

    docs.append(
        node(
            "root",
            "",
            "共计包含500Agent",
            1,
            "root",
            description="大屏智能体总管节点（假数据）",
            creator="system",
            create_time="2026-07-01 10:00:00",
            show=True,
            weight=1000,
            display_count=4,
            account_count=0,
            platform_count=42,
            last_active="刚刚",
            version="v1.0",
        )
    )

    cats = [
        ("cat_collect", "采集智能体", "多平台主页/发文 MCP 与 Apify 采集能力集合", 100, 3, "1 分钟前", 16),
        ("cat_scan", "多平台账号扫描", "跨平台账号发现与扫描智能体", 90, 2, "3 分钟前", 12),
        ("cat_verify", "账号核查智能体", "文本/图片核验与相似账号认定", 80, 2, "5 分钟前", 10),
        ("cat_report", "报告生产智能体", "画像报告与外部智能体 API 编排", 70, 3, "2 分钟前", 8),
    ]
    for cid, name, desc, w, dc, la, pc in cats:
        docs.append(
            node(
                cid,
                "root",
                name,
                2,
                "category",
                description=desc,
                creator="system",
                create_time="2026-07-01 10:05:00",
                show=True,
                weight=w,
                display_count=dc,
                account_count=0,
                platform_count=pc,
                last_active=la,
                version="v1.2",
            )
        )

    # ---------- 采集 L3 + L4 ----------
    collect_agents = [
        ("agent_fb", "Apify-Facebook-MCP采集", 100, True, 3, [
            ("following", "关注列表检索", 1200),
            ("followers", "粉丝列表检索", 980),
            ("user_search", "用户检索", 1500),
            ("profile", "用户主页采集", 2100),
            ("posts", "历史发文采集", 800, False),
            ("keyword", "关键词搜索", 640, False),
            ("groups", "群组信息采集", 420),
            ("pages", "公共主页采集", 560),
        ]),
        ("agent_weixin", "Weixin-MCP采集", 90, True, 3, [
            ("profile", "公众号主页采集", 400),
            ("article", "文章列表采集", 350),
            ("search", "搜一搜检索", 200, False),
            ("video", "视频号采集", 180),
            ("comment", "评论采样采集", 120),
        ]),
        ("agent_bilibili", "BILIBILI-MCP采集", 80, True, 3, [
            ("user", "用户信息采集", 500),
            ("video", "视频列表采集", 480),
            ("dynamic", "动态列表采集", 260),
            ("following", "关注列表采集", 190),
            ("stat", "频道统计采集", 170),
        ]),
        ("agent_reddit", "Reddit-MCP采集", 70, True, 2, [
            ("user", "用户主页采集", 220),
            ("posts", "帖子列表采集", 210),
            ("comments", "评论列表采集", 160),
            ("subreddit", "版块信息采集", 140),
        ]),
        ("agent_youtube", "Youtube-MCP采集", 60, True, 3, [
            ("channel", "频道统计采集", 900),
            ("videos", "视频列表采集", 860),
            ("comments", "评论采样采集", 300),
            ("playlist", "播放列表采集", 240),
            ("search", "频道检索", 280, False),
        ]),
        ("agent_instagram", "Instagram-Apify采集", 50, False, 3, [
            ("profile", "用户主页采集", 540),
            ("posts", "帖子列表采集", 480),
            ("stories", "快拍采样采集", 160, False),
            ("followers", "粉丝列表采集", 220),
            ("following", "关注列表采集", 200),
        ]),
        ("agent_telegram", "Telegram-Apify采集", 40, False, 2, [
            ("channel", "频道主页采集", 310),
            ("posts", "消息列表采集", 280),
            ("members", "成员采样采集", 90, False),
            ("search", "频道检索", 110),
        ]),
        ("agent_tiktok", "TikTok-Apify采集", 55, True, 3, [
            ("profile", "用户主页采集", 620),
            ("videos", "视频列表采集", 580),
            ("comments", "评论采样采集", 210),
            ("followers", "粉丝列表采集", 190, False),
            ("music", "音乐关联采集", 80, False),
        ]),
        ("agent_github", "GitHub-Apify采集", 45, True, 3, [
            ("profile", "用户主页采集", 340),
            ("repos", "仓库列表采集", 300),
            ("stars", "Star 列表采集", 120),
            ("followers", "粉丝列表采集", 100),
        ]),
        ("agent_weibo", "微博-MCP采集", 85, True, 3, [
            ("profile", "用户主页采集", 700),
            ("feeds", "动态列表采集", 650),
            ("followers", "粉丝列表采集", 240),
            ("following", "关注列表采集", 220),
            ("search", "用户检索", 180, False),
        ]),
        ("agent_linkedin", "LinkedIn-Apify采集", 35, False, 2, [
            ("profile", "用户主页采集", 260),
            ("posts", "动态列表采集", 200),
            ("company", "公司主页采集", 150),
            ("search", "人名检索", 130, False),
        ]),
        ("agent_vk", "VK-Apify采集", 30, False, 2, [
            ("profile", "用户主页采集", 180),
            ("posts", "动态列表采集", 160),
            ("friends", "好友列表采集", 90, False),
        ]),
        ("agent_discord", "Discord发现采集", 25, False, 2, [
            ("invite", "邀请链接解析", 80),
            ("guild", "服务器信息采集", 70),
            ("member", "成员采样采集", 50, False),
        ]),
        ("agent_twitch", "Twitch频道采集", 28, False, 2, [
            ("channel", "频道主页采集", 140),
            ("clips", "精彩片段采集", 110),
            ("followers", "粉丝采样采集", 60, False),
        ]),
    ]
    for aid, name, w, show, dc, cap_items in collect_agents:
        docs.append(
            agent(
                aid,
                "cat_collect",
                name,
                show=show,
                weight=w,
                display_count=dc,
                last_active="2 分钟前",
                version="v1.1",
            )
        )
        docs.extend(caps(aid, cap_items, base_weight=100))

    # ---------- 扫描 L3 + L4 ----------
    scan_agents = [
        ("agent_twitter", "Twitter采集", 100, True, 3, [
            ("tweet_detail", "单条推文详情采集", 900),
            ("keyword", "关键词检索", 1100),
            ("raw", "推文原文采集", 3200),
            ("replies", "推文回复列表采集", 700),
            ("profile", "主页采集", 6400),
            ("likes", "点赞列表采集", 400, False),
            ("media", "媒体推文采集", 520),
            ("mentions", "提及检索", 380, False),
        ]),
        ("agent_maigret_scan", "Maigret跨平台扫描", 90, True, 2, [
            ("collect", "用户名跨平台收集", 1200),
            ("sites", "站点命中汇总", 800),
            ("timeout", "超时站点回看", 200, False),
            ("export", "命中结果导出", 150),
        ]),
        ("agent_web_scan", "网页检索扫描", 80, True, 3, [
            ("web_search", "搜索引擎检索", 400),
            ("web_extract", "页面正文抽取", 320),
            ("browser", "浏览器辅助打开", 180, False),
            ("social_site", "社交站点定向检索", 260),
        ]),
        ("agent_handle_norm", "Handle规范化Agent", 70, True, 2, [
            ("normalize", "账号名规范化", 500),
            ("dedupe", "跨平台去重", 420),
            ("alias", "别名映射", 160, False),
        ]),
        ("agent_email_scan", "邮箱线索扫描", 60, True, 2, [
            ("extract", "简介邮箱提取", 240),
            ("domain", "域名关联检索", 180),
            ("verify_fmt", "邮箱格式校验", 100, False),
        ]),
        ("agent_phone_scan", "电话线索扫描", 50, False, 2, [
            ("extract", "简介电话提取", 160),
            ("normalize", "号码规范化", 140),
            ("region", "归属地粗判", 90, False),
        ]),
        ("agent_url_expand", "短链展开扫描", 40, False, 2, [
            ("expand", "短链还原", 220),
            ("safe_check", "危险域过滤", 120, False),
            ("archive", "网页快照存档", 80, False),
        ]),
        ("agent_username_guess", "用户名变体扫描", 55, True, 2, [
            ("mutate", "用户名变体生成", 300),
            ("probe", "平台可用性探测", 260),
            ("rank", "变体优先级排序", 140),
        ]),
        ("agent_geo_hint", "地理线索扫描", 35, False, 2, [
            ("bio_geo", "简介地名抽取", 180),
            ("tz", "时区推断", 100, False),
            ("lang", "语言分布统计", 120),
        ]),
        ("agent_seed_lock", "种子锁定扫描", 95, True, 2, [
            ("parse", "种子句柄解析", 900),
            ("platform", "种子平台识别", 850),
            ("confirm", "种子账号确认", 800),
        ]),
    ]
    for aid, name, w, show, dc, cap_items in scan_agents:
        docs.append(
            agent(aid, "cat_scan", name, show=show, weight=w, display_count=dc)
        )
        docs.extend(caps(aid, cap_items))

    # ---------- 核查 L3 + L4 ----------
    verify_agents = [
        ("agent_text_verify", "文本流核验Agent", 100, True, 2, [
            ("rule", "规则比对核验", 150),
            ("conclusion", "文本核验结论输出", 140),
            ("bio_sim", "简介相似度", 120),
            ("name_sim", "昵称相似度", 110),
            ("link_match", "外链一致性", 90, False),
        ]),
        ("agent_image_verify", "图片流核验Agent", 90, True, 2, [
            ("ocr", "OCR文字提取", 120),
            ("vision", "Vision头像分析", 130),
            ("face", "人脸相似度", 100),
            ("logo", "Logo/水印检测", 80, False),
            ("phash", "感知哈希去重", 70, False),
        ]),
        ("agent_osint_es", "社工库ES核验Agent", 80, False, 2, [
            ("country", "全球身份库检索", 90),
            ("facebook", "Facebook身份库检索", 70),
            ("worldpeople", "全球人口库检索", 60),
            ("url_probe", "URL 命中探测", 50, False),
        ]),
        ("agent_validate", "相似账号认定Agent", 95, True, 2, [
            ("seed_in", "种子必进收敛", 200),
            ("score", "相似度打分", 180),
            ("exclude", "排除账号归档", 100),
            ("report", "认定结果摘要", 90, False),
        ]),
        ("agent_collision", "关联碰撞Agent", 70, True, 2, [
            ("graph", "关系图构建", 160),
            ("bridge", "桥接账号发现", 140),
            ("cluster", "聚类分组", 120),
        ]),
        ("agent_pii_check", "PII风险核验Agent", 60, True, 2, [
            ("mask", "敏感字段脱敏", 110),
            ("leak", "泄露面评估", 100),
            ("policy", "合规策略匹配", 80, False),
        ]),
        ("agent_timeline", "活跃时序核验Agent", 50, True, 2, [
            ("posting", "发文节奏分析", 130),
            ("tz_align", "时区对齐核验", 90),
            ("burst", "突发活跃检测", 70, False),
        ]),
        ("agent_lang_verify", "语言一致性核验", 45, False, 2, [
            ("detect", "语种识别", 100),
            ("mix", "多语混用检测", 80),
            ("script", "文字体系判断", 60, False),
        ]),
        ("agent_link_verify", "外链可信核验", 40, False, 2, [
            ("resolve", "外链解析", 90),
            ("brand", "品牌域匹配", 70),
            ("risk", "高风险域标记", 50, False),
        ]),
    ]
    for aid, name, w, show, dc, cap_items in verify_agents:
        docs.append(
            agent(aid, "cat_verify", name, show=show, weight=w, display_count=dc)
        )
        docs.extend(caps(aid, cap_items))

    # ---------- 报告 L3 + L4 ----------
    report_agents = [
        ("agent_x_api", "X智能体API", 100, True, 2, [
            ("chat", "对话补全", 40),
            ("summarize", "摘要生成", 35),
            ("rewrite", "措辞润色", 30),
            ("translate", "多语翻译", 28, False),
        ]),
        ("agent_google_api", "Google智能体API", 90, True, 2, [
            ("ground", "检索增强生成", 30),
            ("vision", "多模态分析", 28),
            ("code", "结构化抽取", 22),
            ("safety", "安全过滤", 18, False),
        ]),
        ("agent_perplexity", "Perplexity智能体API", 80, True, 2, [
            ("search", "联网问答", 25),
            ("cite", "引用溯源", 22),
            ("news", "时效资讯汇总", 20),
            ("compare", "多源对比", 16, False),
        ]),
        ("agent_google_search", "Google 搜索引擎", 70, True, 2, [
            ("web", "网页检索", 20),
            ("extract", "页面抽取", 18),
            ("image", "图片检索", 12, False),
            ("news", "新闻检索", 14),
        ]),
        ("agent_report_writer", "画像报告生成Agent", 95, True, 3, [
            ("basic", "账号基本信息章节", 50),
            ("views", "观点与涉华分析", 48),
            ("pii", "PII与圈层分析", 45),
            ("img", "图片流分析章节", 40),
            ("final", "终稿组装输出", 55),
        ]),
        ("agent_deepseek", "DeepSeek分析API", 85, True, 2, [
            ("reason", "深度推理", 26),
            ("json", "结构化结论", 24),
            ("zh", "中文长文生成", 22),
        ]),
        ("agent_openai", "OpenAI分析API", 75, True, 2, [
            ("chat", "对话分析", 24),
            ("embed", "向量表征", 20),
            ("moderation", "内容审核", 16, False),
        ]),
        ("agent_chart", "图表叙事Agent", 65, True, 2, [
            ("timeline", "时间线图表", 15),
            ("network", "关系网络图", 14),
            ("stats", "统计卡片", 12),
        ]),
        ("agent_cite_pack", "证据打包Agent", 60, True, 2, [
            ("quote", "原文摘录", 18),
            ("link", "证据链接归档", 16),
            ("hash", "证据指纹", 10, False),
        ]),
        ("agent_review", "报告质检Agent", 55, False, 2, [
            ("fact", "事实一致性检查", 14),
            ("tone", "口径风险检查", 12),
            ("format", "章节格式检查", 11),
        ]),
        ("agent_export", "报告导出Agent", 50, False, 2, [
            ("md", "Markdown 导出", 20),
            ("pdf", "PDF 导出", 15, False),
            ("json", "结构化 JSON 导出", 18),
        ]),
        ("agent_brief", "简报压缩Agent", 45, False, 2, [
            ("one_pager", "一页纸简报", 16),
            ("bullet", "要点列表", 14),
            ("slide", "幻灯提纲", 10, False),
        ]),
    ]
    for aid, name, w, show, dc, cap_items in report_agents:
        docs.append(
            agent(aid, "cat_report", name, show=show, weight=w, display_count=dc)
        )
        docs.extend(caps(aid, cap_items))

    # L3 补齐到 TARGET_L3，并同步挂 L4
    pad_category_l3(docs)
    return docs


# 各大类补齐用「像真的」Agent 名池（够用；不够则带场景后缀循环，禁止「扩展Agent-N」）
# 每项：(id_slug, display_name, description, l4_caps)
# l4_caps: (suffix, name, account_count[, show])
_PAD_SPECS: Dict[str, List[Tuple]] = {
    "cat_collect": [
        ("pinterest", "Pinterest-Apify采集", "Pinterest 图钉/看板采集", [
            ("profile", "用户主页采集", 260), ("pins", "图钉列表采集", 240),
            ("boards", "看板列表采集", 180), ("search", "关键词检索", 150, False),
        ]),
        ("snapchat", "Snapchat-MCP采集", "Snapchat 公开资料采集", [
            ("profile", "用户主页采集", 200), ("stories", "快拍采样", 160),
            ("spotlight", "Spotlight 采集", 120), ("search", "用户检索", 90, False),
        ]),
        ("threads", "Threads-Apify采集", "Meta Threads 动态采集", [
            ("profile", "用户主页采集", 280), ("posts", "帖子列表采集", 250),
            ("replies", "回复列表采集", 140), ("followers", "粉丝采样", 110, False),
        ]),
        ("mastodon", "Mastodon-MCP采集", "联邦宇宙实例采集", [
            ("profile", "用户主页采集", 170), ("statuses", "嘟文列表采集", 160),
            ("followers", "粉丝列表采集", 100), ("instance", "实例信息采集", 80),
        ]),
        ("bluesky", "Bluesky-Apify采集", "Bluesky AT Protocol 采集", [
            ("profile", "用户主页采集", 190), ("feed", "时间线采集", 180),
            ("likes", "点赞列表采集", 90, False), ("search", "帖文检索", 120),
        ]),
        ("truth_social", "TruthSocial-Apify采集", "Truth Social 动态采集", [
            ("profile", "用户主页采集", 150), ("posts", "帖子列表采集", 140),
            ("followers", "粉丝采样", 70, False), ("search", "关键词检索", 85),
        ]),
        ("okru", "OK.ru-Apify采集", "Odnoklassniki 社交采集", [
            ("profile", "用户主页采集", 160), ("posts", "动态列表采集", 140),
            ("friends", "好友列表采集", 90, False), ("groups", "群组采集", 75),
        ]),
        ("qq_zone", "QQ空间-MCP采集", "QQ 空间公开页采集", [
            ("profile", "主页信息采集", 210), ("feeds", "说说列表采集", 190),
            ("photos", "相册采样", 100, False), ("share", "分享流采集", 80),
        ]),
        ("douyin", "抖音-MCP采集", "抖音用户/视频采集", [
            ("profile", "用户主页采集", 520), ("videos", "视频列表采集", 480),
            ("comments", "评论采样", 200), ("followers", "粉丝列表采集", 160, False),
        ]),
        ("xiaohongshu", "小红书-MCP采集", "小红书笔记/用户采集", [
            ("profile", "用户主页采集", 360), ("notes", "笔记列表采集", 340),
            ("comments", "评论采样", 150), ("search", "笔记检索", 180, False),
        ]),
        ("kuaishou", "快手-Apify采集", "快手用户/短视频采集", [
            ("profile", "用户主页采集", 300), ("videos", "视频列表采集", 280),
            ("comments", "评论采样", 120), ("followers", "粉丝采样", 100, False),
        ]),
        ("zhihu", "知乎-MCP采集", "知乎用户/回答采集", [
            ("profile", "用户主页采集", 240), ("answers", "回答列表采集", 220),
            ("articles", "文章列表采集", 160), ("followers", "关注者采集", 110, False),
        ]),
        ("medium", "Medium-Apify采集", "Medium 作者/文章采集", [
            ("profile", "作者主页采集", 140), ("posts", "文章列表采集", 130),
            ("claps", "点赞采样", 60, False), ("publications", "出版物采集", 70),
        ]),
        ("substack", "Substack-MCP采集", "Newsletter 作者采集", [
            ("profile", "作者主页采集", 130), ("posts", "帖文列表采集", 120),
            ("archive", "归档采集", 90), ("about", "About 页采集", 50, False),
        ]),
        ("tumblr", "Tumblr-Apify采集", "Tumblr 博客采集", [
            ("blog", "博客主页采集", 150), ("posts", "博文列表采集", 140),
            ("likes", "喜欢列表采集", 70, False), ("search", "标签检索", 85),
        ]),
        ("flickr", "Flickr-Apify采集", "Flickr 相册/用户采集", [
            ("profile", "用户主页采集", 120), ("photos", "照片列表采集", 110),
            ("albums", "相册列表采集", 80), ("groups", "群组采集", 55, False),
        ]),
        ("vimeo", "Vimeo-MCP采集", "Vimeo 频道/视频采集", [
            ("profile", "用户主页采集", 160), ("videos", "视频列表采集", 150),
            ("channels", "频道采集", 90), ("comments", "评论采样", 60, False),
        ]),
        ("dailymotion", "Dailymotion-Apify采集", "Dailymotion 视频采集", [
            ("profile", "用户主页采集", 140), ("videos", "视频列表采集", 130),
            ("playlists", "播放列表采集", 70), ("comments", "评论采样", 55, False),
        ]),
        ("rumble", "Rumble-Apify采集", "Rumble 频道采集", [
            ("channel", "频道主页采集", 170), ("videos", "视频列表采集", 160),
            ("followers", "粉丝采样", 80, False), ("search", "频道检索", 90),
        ]),
        ("bitchute", "BitChute-MCP采集", "BitChute 频道采集", [
            ("channel", "频道主页采集", 110), ("videos", "视频列表采集", 100),
            ("comments", "评论采样", 45, False), ("search", "检索采集", 60),
        ]),
        ("odysee", "Odysee-Apify采集", "Odysee/LBRY 频道采集", [
            ("channel", "频道主页采集", 125), ("videos", "内容列表采集", 115),
            ("claims", "Claim 解析", 70), ("search", "检索采集", 65, False),
        ]),
        ("soundcloud", "SoundCloud-MCP采集", "SoundCloud 用户/曲目采集", [
            ("profile", "用户主页采集", 180), ("tracks", "曲目列表采集", 170),
            ("playlists", "播放列表采集", 90), ("followers", "粉丝采样", 75, False),
        ]),
        ("spotify", "Spotify-Apify采集", "Spotify 艺人/播客公开页", [
            ("artist", "艺人主页采集", 200), ("albums", "专辑列表采集", 160),
            ("podcast", "播客单集采集", 120), ("search", "曲目检索", 100, False),
        ]),
        ("patreon", "Patreon-MCP采集", "Patreon 创作者公开页", [
            ("creator", "创作者主页采集", 140), ("posts", "公开帖采集", 100),
            ("tiers", "档位信息采集", 70, False), ("about", "简介采集", 55),
        ]),
        ("onlyfans_pub", "OnlyFans公开页-Apify", "公开资料页采集（合规公开信息）", [
            ("profile", "公开主页采集", 90), ("posts", "公开帖采样", 70, False),
            ("media", "媒体元数据采集", 60), ("links", "外链提取", 50),
        ]),
        ("quora", "Quora-Apify采集", "Quora 用户/回答采集", [
            ("profile", "用户主页采集", 150), ("answers", "回答列表采集", 140),
            ("spaces", "Space 采集", 80), ("search", "问答检索", 95, False),
        ]),
        ("stackoverflow", "StackOverflow-MCP采集", "SO 用户/回答采集", [
            ("profile", "用户主页采集", 160), ("answers", "回答列表采集", 150),
            ("questions", "提问列表采集", 120), ("badges", "徽章统计", 70, False),
        ]),
        ("hackernews", "HackerNews-MCP采集", "HN 用户/评论采集", [
            ("user", "用户主页采集", 130), ("submissions", "提交列表采集", 120),
            ("comments", "评论列表采集", 110), ("threads", "主题串采集", 85, False),
        ]),
        ("producthunt", "ProductHunt-Apify采集", "PH 产品/用户采集", [
            ("profile", "用户主页采集", 100), ("launches", "产品发布采集", 90),
            ("comments", "评论采样", 60), ("makers", "Maker 关联采集", 55, False),
        ]),
        ("behance", "Behance-Apify采集", "Behance 作品集采集", [
            ("profile", "用户主页采集", 140), ("projects", "项目列表采集", 130),
            ("appreciations", "点赞采样", 70, False), ("moodboards", "灵感板采集", 60),
        ]),
        ("dribbble", "Dribbble-MCP采集", "Dribbble 作品采集", [
            ("profile", "用户主页采集", 135), ("shots", "作品列表采集", 125),
            ("likes", "喜欢列表采集", 65, False), ("buckets", "收藏夹采集", 55),
        ]),
        ("artstation", "ArtStation-Apify采集", "ArtStation 作品采集", [
            ("profile", "用户主页采集", 145), ("projects", "项目列表采集", 135),
            ("prints", "Print 商店采集", 60, False), ("likes", "喜欢采样", 50),
        ]),
        ("deviantart", "DeviantArt-MCP采集", "DeviantArt 作品采集", [
            ("profile", "用户主页采集", 155), ("deviations", "作品列表采集", 145),
            ("favourites", "收藏采样", 70, False), ("groups", "小组采集", 55),
        ]),
        ("goodreads", "Goodreads-Apify采集", "Goodreads 书架/用户采集", [
            ("profile", "用户主页采集", 120), ("shelves", "书架列表采集", 110),
            ("reviews", "书评采样", 80), ("friends", "好友采样", 50, False),
        ]),
        ("letterboxd", "Letterboxd-MCP采集", "影评/片单采集", [
            ("profile", "用户主页采集", 115), ("films", "观影列表采集", 105),
            ("reviews", "影评采样", 75), ("lists", "片单采集", 65, False),
        ]),
        ("steam", "Steam社区-Apify采集", "Steam 个人资料采集", [
            ("profile", "用户主页采集", 190), ("games", "游戏库采集", 170),
            ("friends", "好友列表采集", 100, False), ("reviews", "评测采样", 80),
        ]),
        ("xbox", "Xbox档案-MCP采集", "Xbox 公开档案采集", [
            ("profile", "玩家档案采集", 130), ("games", "游戏列表采集", 120),
            ("achievements", "成就采样", 70), ("clips", "精彩瞬间采集", 55, False),
        ]),
        ("psn", "PSN档案-Apify采集", "PlayStation 公开档案采集", [
            ("profile", "玩家档案采集", 125), ("games", "游戏列表采集", 115),
            ("trophies", "奖杯采样", 75), ("friends", "好友采样", 50, False),
        ]),
        ("roblox", "Roblox-MCP采集", "Roblox 用户/作品采集", [
            ("profile", "用户主页采集", 210), ("games", "体验列表采集", 160),
            ("friends", "好友采样", 90, False), ("badges", "徽章采集", 70),
        ]),
        ("wikipedia", "Wikipedia用户-MCP", "维基用户页/贡献采集", [
            ("user", "用户页采集", 100), ("contribs", "贡献列表采集", 140),
            ("talk", "讨论页采集", 60, False), ("blocks", "封禁记录采集", 40),
        ]),
        ("fandom", "Fandom-Apify采集", "Fandom Wiki 用户采集", [
            ("user", "用户页采集", 90), ("contribs", "贡献列表采集", 110),
            ("blogs", "博客采集", 55, False), ("walls", "留言墙采集", 45),
        ]),
        ("notion_pub", "Notion公开页-MCP", "公开 Notion 页采集", [
            ("page", "页面正文采集", 80), ("database", "公开库采集", 70),
            ("links", "外链提取", 50), ("meta", "元数据解析", 40, False),
        ]),
        ("gitbook", "GitBook-Apify采集", "GitBook 文档站采集", [
            ("site", "站点地图采集", 85), ("pages", "文档页采集", 95),
            ("search", "站内检索", 60, False), ("meta", "版本信息采集", 45),
        ]),
        ("mirror", "Mirror.xyz-MCP采集", "Web3 写作平台采集", [
            ("profile", "作者主页采集", 100), ("entries", "文章列表采集", 90),
            ("collect", "Collect 记录", 55, False), ("dao", "DAO 关联采集", 50),
        ]),
        ("lens", "Lens Protocol-Apify", "Lens 社交图谱采集", [
            ("profile", "Profile 采集", 110), ("publications", "内容列表采集", 100),
            ("followers", "粉丝采样", 70), ("mirrors", "Mirror 记录", 55, False),
        ]),
        ("farcaster", "Farcaster-MCP采集", "Farcaster 用户/Cast 采集", [
            ("profile", "用户主页采集", 130), ("casts", "Cast 列表采集", 120),
            ("channels", "频道采集", 80), ("followers", "粉丝采样", 65, False),
        ]),
        ("nostr", "Nostr-Apify采集", "Nostr 公钥/事件采集", [
            ("profile", "资料事件采集", 95), ("notes", "笔记事件采集", 105),
            ("relays", "中继列表采集", 70), ("contacts", "联系人采集", 55, False),
        ]),
        ("line", "LINE官方账号-MCP", "LINE OA 公开页采集", [
            ("oa", "官方账号主页", 140), ("timeline", "时间线采样", 100),
            ("stickers", "贴纸信息采集", 60, False), ("links", "外链提取", 50),
        ]),
        ("kakao", "KakaoStory-Apify采集", "Kakao 公开动态采集", [
            ("profile", "用户主页采集", 120), ("posts", "动态列表采集", 110),
            ("friends", "好友采样", 60, False), ("search", "检索采集", 70),
        ]),
        ("naver", "Naver Cafe-MCP采集", "Naver Cafe/博客采集", [
            ("blog", "博客主页采集", 150), ("posts", "博文列表采集", 140),
            ("cafe", "Cafe 会员页", 90), ("comments", "评论采样", 65, False),
        ]),
        ("baidu_tieba", "百度贴吧-MCP采集", "贴吧用户/帖子采集", [
            ("profile", "用户主页采集", 200), ("posts", "发帖列表采集", 180),
            ("bars", "关注吧采集", 100), ("replies", "回复采样", 85, False),
        ]),
        ("wechat_channels", "微信视频号-Apify", "视频号公开内容采集", [
            ("profile", "账号主页采集", 220), ("videos", "视频列表采集", 200),
            ("live", "直播预告采集", 80, False), ("comments", "评论采样", 90),
        ]),
        ("netease_music", "网易云音乐-MCP", "网易云用户/歌单采集", [
            ("profile", "用户主页采集", 170), ("playlists", "歌单列表采集", 160),
            ("follows", "关注列表采集", 80, False), ("comments", "评论采样", 70),
        ]),
        ("qq_music", "QQ音乐-Apify采集", "QQ 音乐公开页采集", [
            ("profile", "用户主页采集", 155), ("playlists", "歌单采集", 145),
            ("follows", "关注采样", 70, False), ("comments", "评论采样", 60),
        ]),
        ("bilibili_bangumi", "B站番剧-MCP采集", "番剧/追剧用户侧数据", [
            ("user", "追番列表采集", 180), ("reviews", "短评采样", 90),
            ("timeline", "更新时间线", 100), ("rank", "排行榜采集", 110, False),
        ]),
        ("acfun", "AcFun-Apify采集", "A站用户/稿件采集", [
            ("profile", "用户主页采集", 140), ("videos", "稿件列表采集", 130),
            ("banana", "香蕉统计采集", 60, False), ("comments", "评论采样", 70),
        ]),
        ("ixigua", "西瓜视频-MCP采集", "西瓜用户/视频采集", [
            ("profile", "用户主页采集", 160), ("videos", "视频列表采集", 150),
            ("followers", "粉丝采样", 80, False), ("search", "检索采集", 90),
        ]),
        ("toutiao", "今日头条-Apify采集", "头条号公开页采集", [
            ("profile", "头条号主页", 190), ("articles", "文章列表采集", 180),
            ("videos", "视频列表采集", 120), ("followers", "粉丝采样", 90, False),
        ]),
        ("sohu", "搜狐号-MCP采集", "搜狐号文章采集", [
            ("profile", "账号主页采集", 110), ("articles", "文章列表采集", 100),
            ("comments", "评论采样", 50, False), ("search", "检索采集", 60),
        ]),
        ("netease_news", "网易号-Apify采集", "网易号公开内容采集", [
            ("profile", "账号主页采集", 115), ("articles", "文章列表采集", 105),
            ("comments", "评论采样", 55, False), ("topics", "话题采集", 65),
        ]),
        ("ifeng", "凤凰号-MCP采集", "凤凰号内容采集", [
            ("profile", "账号主页采集", 105), ("articles", "文章列表采集", 95),
            ("videos", "视频采样", 60), ("comments", "评论采样", 45, False),
        ]),
        ("yandex_zen", "Yandex Zen-Apify", "Zen 频道采集", [
            ("channel", "频道主页采集", 125), ("posts", "内容列表采集", 115),
            ("subscribers", "订阅采样", 70, False), ("search", "检索采集", 75),
        ]),
        ("rutube", "Rutube-MCP采集", "Rutube 频道采集", [
            ("channel", "频道主页采集", 130), ("videos", "视频列表采集", 120),
            ("comments", "评论采样", 55, False), ("playlists", "播放列表", 65),
        ]),
        ("nicovideo", "Niconico-Apify采集", "Niconico 用户/動画采集", [
            ("profile", "用户主页采集", 145), ("videos", "動画列表采集", 135),
            ("mylists", "マイリスト采集", 80), ("comments", "弹幕采样", 70, False),
        ]),
    ],
    "cat_scan": [
        ("sherlock", "Sherlock用户名扫描", "多站点用户名占用探测", [
            ("probe", "站点可用性探测", 400), ("export", "命中导出", 120),
            ("timeout", "超时站点回看", 80, False), ("rank", "命中排序", 100),
        ]),
        ("whatsmyname", "WhatsMyName扫描", "社区规则库用户名扫描", [
            ("rules", "规则库匹配", 350), ("sites", "站点命中汇总", 200),
            ("diff", "增量对比", 90, False), ("export", "结果导出", 110),
        ]),
        ("socialscan", "SocialScan邮箱扫描", "邮箱/用户名社交占用检查", [
            ("email", "邮箱占用探测", 280), ("username", "用户名探测", 260),
            ("providers", "服务商覆盖", 140), ("cache", "结果缓存", 70, False),
        ]),
        ("holehe", "Holehe邮箱注册扫描", "邮箱在各站注册痕迹", [
            ("check", "注册痕迹探测", 300), ("providers", "服务清单扫描", 180),
            ("export", "命中导出", 90), ("retry", "失败重试", 60, False),
        ]),
        ("ghunt", "GHunt谷歌痕迹扫描", "Google 账号公开痕迹", [
            ("maps", "Maps 贡献扫描", 150), ("youtube", "YouTube 关联", 140),
            ("calendar", "日历公开信息", 80, False), ("profile", "资料聚合", 120),
        ]),
        ("epieos", "Epieos邮箱OSINT", "邮箱反向公开信息", [
            ("gravatar", "Gravatar 关联", 160), ("google", "Google 痕迹", 150),
            ("domains", "域名关联", 100), ("export", "结果打包", 70, False),
        ]),
        ("domain_whois", "域名Whois扫描", "域名注册信息检索", [
            ("whois", "Whois 查询", 200), ("history", "历史快照", 120),
            ("dns", "DNS 记录采集", 140), ("rdap", "RDAP 查询", 90, False),
        ]),
        ("crtsh", "证书透明度扫描", "crt.sh 子域发现", [
            ("subdomain", "子域枚举", 260), ("cert", "证书详情", 130),
            ("filter", "噪声过滤", 80), ("export", "列表导出", 70, False),
        ]),
        ("wayback", "Wayback快照扫描", "互联网档案馆历史页", [
            ("cdx", "CDX 索引检索", 220), ("snapshot", "快照抓取", 180),
            ("diff", "版本对比", 90, False), ("export", "归档导出", 80),
        ]),
        ("archive_today", "今日归档扫描", "archive.today 镜像检索", [
            ("search", "镜像检索", 160), ("fetch", "归档页抓取", 140),
            ("meta", "元数据解析", 70), ("retry", "失败重试", 50, False),
        ]),
        ("google_dork", "Google语法扫描", "定向 dork 检索", [
            ("dork", "语法查询", 240), ("site", "站内定向", 200),
            ("filetype", "文件类型过滤", 120), ("export", "结果导出", 90, False),
        ]),
        ("bing_dork", "Bing语法扫描", "Bing 定向检索", [
            ("dork", "语法查询", 180), ("site", "站内定向", 160),
            ("news", "新闻垂类", 100), ("export", "结果导出", 70, False),
        ]),
        ("yandex_dork", "Yandex语法扫描", "Yandex 定向检索", [
            ("dork", "语法查询", 150), ("site", "站内定向", 130),
            ("images", "图片垂类", 90, False), ("export", "结果导出", 60),
        ]),
        ("duckduckgo", "DuckDuckGo扫描", "DDG 公开检索", [
            ("web", "网页检索", 170), ("instant", "Instant Answer", 80),
            ("site", "站内定向", 110), ("export", "结果导出", 65, False),
        ]),
        ("serp_api", "SERP聚合扫描", "多引擎 SERP 聚合", [
            ("google", "Google SERP", 210), ("bing", "Bing SERP", 180),
            ("dedupe", "结果去重", 100), ("rank", "排名跟踪", 90, False),
        ]),
        ("reverse_image", "以图搜图扫描", "反向图片检索", [
            ("google", "Google 以图搜图", 190), ("yandex", "Yandex 以图搜图", 170),
            ("tineye", "TinEye 检索", 120), ("meta", "EXIF 粗解析", 70, False),
        ]),
        ("face_cluster", "人脸聚类扫描", "头像跨帖聚类线索", [
            ("embed", "人脸向量化", 140), ("cluster", "聚类分组", 130),
            ("match", "近似匹配", 110), ("export", "簇导出", 60, False),
        ]),
        ("phash_scan", "感知哈希扫描", "图片 phash 去重/追踪", [
            ("hash", "感知哈希计算", 160), ("dup", "重复检测", 140),
            ("track", "传播追踪", 100), ("export", "指纹库导出", 70, False),
        ]),
        ("ocr_bio", "简介OCR扫描", "头像/封面文字抽取", [
            ("ocr", "文字识别", 150), ("lang", "语种识别", 90),
            ("pii", "PII 粗提取", 110), ("export", "文本导出", 60, False),
        ]),
        ("qr_scan", "二维码线索扫描", "图中二维码解析", [
            ("detect", "二维码检测", 100), ("decode", "内容解码", 90),
            ("url", "URL 展开", 80), ("safe", "危险域过滤", 50, False),
        ]),
        ("exif_scan", "EXIF元数据扫描", "图片元数据提取", [
            ("exif", "EXIF 解析", 120), ("gps", "GPS 粗定位", 80, False),
            ("device", "设备型号提取", 70), ("strip", "敏感字段标记", 55),
        ]),
        ("pdf_meta", "PDF元数据扫描", "文档作者/软件痕迹", [
            ("meta", "元数据解析", 110), ("author", "作者字段提取", 90),
            ("xmp", "XMP 解析", 70), ("export", "字段导出", 50, False),
        ]),
        ("torrent_osint", "BT指纹扫描", "公开 BT/磁力关联", [
            ("magnet", "磁力解析", 90), ("tracker", "Tracker 查询", 80),
            ("peer", "Peer 采样", 60, False), ("export", "结果导出", 45),
        ]),
        ("paste_scan", "粘贴站泄露扫描", "Pastebin 类站点检索", [
            ("search", "关键词检索", 200), ("fetch", "原文抓取", 150),
            ("pii", "PII 命中", 120), ("alert", "高危告警", 70, False),
        ]),
        ("github_code", "GitHub代码泄露扫描", "代码仓敏感串检索", [
            ("code", "代码检索", 240), ("gist", "Gist 检索", 160),
            ("secret", "密钥模式匹配", 180), ("export", "命中导出", 90, False),
        ]),
        ("gitlab_leak", "GitLab泄露扫描", "公开 GitLab 敏感检索", [
            ("code", "代码检索", 170), ("issues", "Issue 检索", 120),
            ("secret", "密钥模式匹配", 140), ("export", "命中导出", 70, False),
        ]),
        ("dockerhub", "DockerHub扫描", "镜像/用户公开信息", [
            ("user", "用户主页采集", 100), ("repos", "仓库列表", 110),
            ("tags", "Tag 列表", 90), ("layers", "层信息采样", 55, False),
        ]),
        ("npm_pkg", "NPM包作者扫描", "npm 包与维护者关联", [
            ("pkg", "包元数据采集", 130), ("maintainer", "维护者解析", 120),
            ("deps", "依赖图谱", 90), ("export", "结果导出", 60, False),
        ]),
        ("pypi_pkg", "PyPI包作者扫描", "PyPI 项目与作者关联", [
            ("pkg", "包元数据采集", 125), ("author", "作者解析", 115),
            ("files", "发行文件列表", 80), ("export", "结果导出", 55, False),
        ]),
        ("maven_pkg", "Maven构件扫描", "Maven 坐标与组织关联", [
            ("artifact", "构件元数据", 110), ("group", "Group 解析", 100),
            ("versions", "版本列表", 85), ("export", "结果导出", 50, False),
        ]),
        ("appstore", "AppStore开发者扫描", "iOS 应用开发者页", [
            ("dev", "开发者主页", 140), ("apps", "应用列表", 150),
            ("reviews", "评论采样", 80, False), ("meta", "应用元数据", 90),
        ]),
        ("google_play", "GooglePlay开发者扫描", "安卓应用开发者页", [
            ("dev", "开发者主页", 145), ("apps", "应用列表", 155),
            ("reviews", "评论采样", 85, False), ("meta", "应用元数据", 95),
        ]),
        ("apk_meta", "APK元数据扫描", "安装包证书/包名", [
            ("cert", "签名证书解析", 100), ("pkg", "包名提取", 90),
            ("perm", "权限列表", 80), ("export", "报告导出", 50, False),
        ]),
        ("telegram_osint", "Telegram用户名扫描", "TG 用户名/公开群探测", [
            ("username", "用户名探测", 220), ("channel", "公开频道探测", 200),
            ("invite", "邀请链接解析", 140), ("export", "命中导出", 80, False),
        ]),
        ("discord_invite", "Discord邀请扫描", "邀请码解析与服务器线索", [
            ("invite", "邀请解析", 180), ("guild", "服务器信息", 160),
            ("vanity", "Vanity URL 探测", 100), ("export", "结果导出", 70, False),
        ]),
        ("slack_workspace", "Slack工作区扫描", "公开 Slack 工作区探测", [
            ("workspace", "工作区探测", 120), ("team", "Team 信息", 100),
            ("icons", "图标采集", 60, False), ("export", "结果导出", 50),
        ]),
        ("ms_teams", "Teams公开链扫描", "Teams 公开链接解析", [
            ("link", "链接解析", 90), ("tenant", "租户线索", 80),
            ("safe", "风险域过滤", 55, False), ("export", "结果导出", 45),
        ]),
    ],
    "cat_verify": [
        ("voiceprint", "声纹一致性核验", "音频声纹近似比对", [
            ("embed", "声纹向量化", 80), ("match", "相似度打分", 75),
            ("cluster", "说话人聚类", 60), ("report", "结论输出", 50, False),
        ]),
        ("deepfake", "深度伪造核验", "影像伪造痕迹检测", [
            ("face", "换脸痕迹检测", 90), ("audio", "音频伪造检测", 70),
            ("score", "风险打分", 65), ("report", "结论输出", 55, False),
        ]),
        ("watermark", "水印溯源核验", "平台水印/台标识别", [
            ("detect", "水印检测", 85), ("brand", "台标匹配", 75),
            ("crop", "裁剪还原", 50, False), ("report", "结论输出", 45),
        ]),
        ("stego", "隐写痕迹核验", "隐写/嵌入痕迹粗检", [
            ("scan", "隐写扫描", 60), ("entropy", "熵异常检测", 55),
            ("meta", "容器元数据", 40, False), ("report", "结论输出", 35),
        ]),
        ("device_fp", "设备指纹核验", "UA/设备特征一致性", [
            ("ua", "UA 解析", 70), ("screen", "分辨率特征", 50),
            ("tz", "时区一致性", 55), ("report", "结论输出", 40, False),
        ]),
        ("ip_geo", "IP归属核验", "登录地/发帖地粗核", [
            ("geo", "归属地查询", 100), ("asn", "ASN 解析", 80),
            ("vpn", "代理特征标记", 70), ("report", "结论输出", 50, False),
        ]),
        ("timezone_align", "时区对齐核验Plus", "活跃时区交叉验证", [
            ("posting", "发帖时序", 90), ("login", "登录时序", 70, False),
            ("align", "时区对齐", 80), ("report", "结论输出", 45),
        ]),
        ("keyboard_layout", "键盘布局核验", "输入习惯/错拼模式", [
            ("typo", "错拼模式", 55), ("layout", "布局推断", 50),
            ("lang", "语种习惯", 60), ("report", "结论输出", 40, False),
        ]),
        ("emoji_style", "表情风格核验", "表情使用习惯比对", [
            ("freq", "表情频次", 50), ("combo", "组合模式", 45),
            ("lang", "文化圈标记", 40), ("report", "结论输出", 35, False),
        ]),
        ("hashtag_style", "话题标签核验", "标签偏好一致性", [
            ("tag", "标签抽取", 65), ("topic", "主题聚类", 60),
            ("cross", "跨平台对齐", 55), ("report", "结论输出", 40, False),
        ]),
        ("link_graph", "外链图谱核验", "外链域名关系图", [
            ("extract", "外链抽取", 85), ("graph", "域名图构建", 75),
            ("brand", "品牌域匹配", 65), ("report", "结论输出", 50, False),
        ]),
        ("bio_template", "简介模板核验", "简介套话/模板检测", [
            ("template", "模板匹配", 70), ("sim", "相似度", 65),
            ("lang", "多语模板", 55), ("report", "结论输出", 45, False),
        ]),
        ("avatar_recycle", "头像复用核验", "跨账号头像复用", [
            ("hash", "头像指纹", 90), ("match", "复用匹配", 85),
            ("cluster", "复用簇", 70), ("report", "结论输出", 55, False),
        ]),
        ("banner_recycle", "封面图复用核验", "封面/背景图复用", [
            ("hash", "封面指纹", 75), ("match", "复用匹配", 70),
            ("crop", "裁剪变体", 50, False), ("report", "结论输出", 45),
        ]),
        ("name_variant", "昵称变体核验", "昵称/译名变体对齐", [
            ("norm", "规范化", 80), ("variant", "变体生成比对", 75),
            ("script", "文字体系", 60), ("report", "结论输出", 50, False),
        ]),
        ("handle_variant", "句柄变体核验", "用户名变体一致性", [
            ("mutate", "变体展开", 85), ("probe", "占用探测", 80),
            ("score", "一致性打分", 70), ("report", "结论输出", 55, False),
        ]),
        ("email_pattern", "邮箱模式核验", "邮箱命名规律核验", [
            ("pattern", "模式提取", 65), ("domain", "域名可信", 60),
            ("role", "角色邮箱识别", 50), ("report", "结论输出", 40, False),
        ]),
        ("phone_pattern", "号码模式核验", "电话格式/归属核验", [
            ("norm", "号码规范化", 70), ("region", "归属粗判", 65),
            ("voip", "VoIP 特征", 45, False), ("report", "结论输出", 40),
        ]),
        ("id_card_mask", "证件脱敏核验", "证件号展示合规检查", [
            ("detect", "证件号检测", 55), ("mask", "脱敏检查", 60),
            ("policy", "策略匹配", 50), ("report", "结论输出", 40, False),
        ]),
        ("child_safety", "未成年人风险核验", "内容未成年人风险标记", [
            ("detect", "风险检测", 70), ("policy", "策略匹配", 65),
            ("escalate", "升级标记", 50), ("report", "结论输出", 45, False),
        ]),
        ("hate_speech", "仇恨言论核验", "仇恨/极端表述检测", [
            ("detect", "表述检测", 80), ("lang", "多语覆盖", 70),
            ("score", "风险打分", 65), ("report", "结论输出", 50, False),
        ]),
        ("scam_pattern", "诈骗话术核验", "常见诈骗模板匹配", [
            ("template", "话术模板", 85), ("link", "钓鱼链检测", 80),
            ("score", "风险打分", 70), ("report", "结论输出", 55, False),
        ]),
        ("bot_behavior", "机器人行为核验", "发帖节奏/机器特征", [
            ("rhythm", "发帖节奏", 90), ("burst", "突发检测", 75),
            ("graph", "互关异常", 70), ("report", "结论输出", 55, False),
        ]),
        ("sockpuppet", "马甲关联核验", "疑似马甲关联", [
            ("feature", "特征对齐", 85), ("graph", "关系图", 80),
            ("score", "关联打分", 75), ("report", "结论输出", 60, False),
        ]),
        ("co_post", "共发行为核验", "同步发帖/转发模式", [
            ("sync", "同步窗口", 70), ("retweet", "转发链", 65),
            ("cluster", "共发簇", 60), ("report", "结论输出", 45, False),
        ]),
        ("quote_tamper", "引文篡改核验", "引用原文一致性", [
            ("align", "原文对齐", 75), ("diff", "差异检测", 70),
            ("cite", "引用溯源", 65), ("report", "结论输出", 50, False),
        ]),
        ("translation_drift", "译文漂移核验", "跨语译文一致性", [
            ("align", "双语对齐", 65), ("drift", "语义漂移", 60),
            ("term", "术语一致性", 55), ("report", "结论输出", 40, False),
        ]),
        ("fact_claim", "事实主张核验", "可核验主张抽取", [
            ("claim", "主张抽取", 80), ("check", "事实核对", 75),
            ("source", "来源匹配", 70), ("report", "结论输出", 55, False),
        ]),
        ("source_trust", "信源可信核验", "来源域可信评级", [
            ("domain", "域名评级", 85), ("hist", "历史信誉", 70),
            ("mirror", "镜像站识别", 60), ("report", "结论输出", 50, False),
        ]),
        ("entity_link", "实体链接核验", "人名/机构实体对齐", [
            ("ner", "实体识别", 90), ("link", "实体链接", 85),
            ("wiki", "知识库对齐", 75), ("report", "结论输出", 60, False),
        ]),
        ("event_align", "事件对齐核验", "跨源事件时间对齐", [
            ("event", "事件抽取", 80), ("time", "时间对齐", 75),
            ("place", "地点对齐", 70), ("report", "结论输出", 55, False),
        ]),
        ("image_text", "图文一致性核验", "配图与正文一致性", [
            ("ocr", "图内文字", 85), ("align", "图文对齐", 80),
            ("mismatch", "不一致标记", 70), ("report", "结论输出", 55, False),
        ]),
        ("video_keyframe", "视频关键帧核验", "关键帧与描述一致性", [
            ("frame", "关键帧抽取", 75), ("caption", "描述比对", 70),
            ("scene", "场景分类", 60), ("report", "结论输出", 50, False),
        ]),
    ],
    "cat_report": [
        ("claude_api", "Claude分析API", "Anthropic Claude 分析编排", [
            ("chat", "对话分析", 30), ("long", "长文总结", 28),
            ("json", "结构化抽取", 24), ("safety", "安全过滤", 18, False),
        ]),
        ("gemini_api", "Gemini分析API", "Google Gemini 多模态分析", [
            ("chat", "对话分析", 28), ("vision", "多模态分析", 26),
            ("ground", "检索增强", 22), ("safety", "安全过滤", 16, False),
        ]),
        ("grok_api", "Grok分析API", "xAI Grok 分析编排", [
            ("chat", "对话分析", 26), ("search", "实时检索增强", 24),
            ("json", "结构化结论", 20), ("zh", "中文长文", 18, False),
        ]),
        ("qwen_api", "通义千问分析API", "Qwen 中文分析编排", [
            ("chat", "对话分析", 27), ("zh", "中文长文", 25),
            ("json", "结构化结论", 21), ("safety", "安全过滤", 15, False),
        ]),
        ("ernie_api", "文心一言分析API", "ERNIE 中文分析编排", [
            ("chat", "对话分析", 24), ("zh", "中文长文", 22),
            ("json", "结构化结论", 18), ("search", "检索增强", 16, False),
        ]),
        ("moonshot_api", "Kimi分析API", "Moonshot 长上下文分析", [
            ("chat", "对话分析", 25), ("long", "长上下文总结", 27),
            ("json", "结构化结论", 19), ("file", "文档理解", 17, False),
        ]),
        ("mistral_api", "Mistral分析API", "Mistral 多语分析", [
            ("chat", "对话分析", 22), ("translate", "多语翻译", 20),
            ("json", "结构化结论", 18), ("code", "结构化抽取", 15, False),
        ]),
        ("cohere_api", "Cohere分析API", "Cohere 检索/重排", [
            ("embed", "向量表征", 20), ("rerank", "结果重排", 22),
            ("chat", "对话分析", 18), ("json", "结构化结论", 14, False),
        ]),
        ("voyage_embed", "Voyage向量API", "文档向量化与检索", [
            ("embed", "向量表征", 21), ("index", "索引写入", 16),
            ("search", "向量检索", 18), ("batch", "批量编码", 12, False),
        ]),
        ("bing_search", "Bing搜索引擎", "Bing 网页检索增强", [
            ("web", "网页检索", 19), ("news", "新闻检索", 15),
            ("image", "图片检索", 12, False), ("extract", "页面抽取", 14),
        ]),
        ("brave_search", "Brave搜索引擎", "Brave Search 检索", [
            ("web", "网页检索", 17), ("news", "新闻检索", 13),
            ("image", "图片检索", 10, False), ("extract", "页面抽取", 12),
        ]),
        ("searxng", "SearXNG聚合搜索", "自建聚合检索", [
            ("web", "网页检索", 16), ("meta", "多引擎聚合", 15),
            ("filter", "结果过滤", 11), ("extract", "页面抽取", 10, False),
        ]),
        ("tavily", "Tavily检索API", "Agent 专用网页检索", [
            ("search", "网页检索", 18), ("extract", "页面抽取", 16),
            ("news", "时效资讯", 14), ("cite", "引用打包", 12, False),
        ]),
        ("exa_search", "Exa神经检索", "语义网页检索", [
            ("search", "语义检索", 17), ("similar", "相似页发现", 14),
            ("extract", "页面抽取", 13), ("cite", "引用打包", 11, False),
        ]),
        ("firecrawl_report", "Firecrawl写报增强", "站点清洗入库写报", [
            ("crawl", "站点爬取", 20), ("clean", "正文清洗", 18),
            ("md", "Markdown 输出", 16), ("map", "站点地图", 12, False),
        ]),
        ("jina_reader", "Jina Reader写报", "URL 转干净正文", [
            ("read", "正文读取", 19), ("md", "Markdown 输出", 17),
            ("batch", "批量转换", 13), ("meta", "元数据保留", 10, False),
        ]),
        ("report_outline", "报告大纲Agent", "章节大纲自动生成", [
            ("outline", "大纲生成", 22), ("section", "章节拆分", 20),
            ("todo", "写作待办", 14), ("review", "大纲质检", 12, False),
        ]),
        ("report_merge", "多源合并Agent", "多源素材合并去重", [
            ("merge", "素材合并", 21), ("dedupe", "去重", 19),
            ("conflict", "冲突标记", 15), ("index", "素材索引", 13, False),
        ]),
        ("timeline_writer", "时间线写作Agent", "事件时间线成稿", [
            ("extract", "事件抽取", 18), ("sort", "时间排序", 17),
            ("narrative", "叙事成稿", 16), ("cite", "证据挂接", 12, False),
        ]),
        ("persona_writer", "人物小传Agent", "人物小传章节生成", [
            ("bio", "小传生成", 20), ("career", "经历梳理", 16),
            ("network", "关系简述", 14), ("risk", "风险提示", 11, False),
        ]),
        ("risk_matrix", "风险矩阵Agent", "风险项矩阵输出", [
            ("score", "风险打分", 19), ("matrix", "矩阵表", 17),
            ("mitigate", "处置建议", 13), ("export", "表格导出", 12, False),
        ]),
        ("compare_table", "对比表Agent", "多账号对比表", [
            ("table", "对比表生成", 18), ("diff", "差异高亮", 16),
            ("rank", "指标排序", 14), ("export", "表格导出", 12, False),
        ]),
        ("quote_card", "金句卡片Agent", "重点引语卡片", [
            ("quote", "引语抽取", 15), ("card", "卡片排版", 14),
            ("translate", "译文对照", 11), ("export", "图片导出", 9, False),
        ]),
        ("map_viz", "地理可视化Agent", "地点线索地图", [
            ("geo", "地点抽取", 14), ("map", "地图图层", 13),
            ("cluster", "热点聚合", 11), ("export", "图层导出", 9, False),
        ]),
        ("network_viz", "关系网可视化Agent", "账号关系网图", [
            ("graph", "关系构图", 16), ("layout", "布局算法", 13),
            ("filter", "边过滤", 11), ("export", "图导出", 10, False),
        ]),
        ("slide_deck", "汇报幻灯Agent", "汇报用幻灯提纲", [
            ("deck", "幻灯生成", 15), ("speaker", "讲稿要点", 13),
            ("chart", "插图建议", 10), ("export", "PPTX 导出", 12, False),
        ]),
        ("exec_summary", "高管摘要Agent", "一页纸高管摘要", [
            ("summary", "摘要生成", 20), ("bullets", "要点列表", 18),
            ("risk", "风险置顶", 14), ("translate", "英译摘要", 12, False),
        ]),
        ("zh_en_bilingual", "中英双语报告Agent", "中英对照终稿", [
            ("zh", "中文成稿", 19), ("en", "英文成稿", 18),
            ("align", "段落对齐", 14), ("qa", "双语质检", 11, False),
        ]),
        ("redteam_review", "红队质检Agent", "对抗式报告挑错", [
            ("attack", "挑错提问", 14), ("gap", "证据缺口", 13),
            ("bias", "偏见检查", 12), ("fix", "修订清单", 11, False),
        ]),
        ("compliance_review", "合规审阅Agent", "出境/隐私合规审阅", [
            ("pii", "PII 审阅", 16), ("policy", "策略匹配", 15),
            ("mask", "脱敏建议", 13), ("signoff", "审阅签字栏", 10, False),
        ]),
        ("version_diff", "报告版本对比Agent", "多版终稿差异", [
            ("diff", "文本差异", 15), ("track", "变更追踪", 14),
            ("approve", "审批记录", 10), ("export", "差异导出", 11, False),
        ]),
        ("template_fill", "模板填报Agent", "固定模板字段填充", [
            ("map", "字段映射", 17), ("fill", "自动填报", 16),
            ("validate", "必填校验", 13), ("export", "模板导出", 12, False),
        ]),
        ("annex_pack", "附件打包Agent", "证据附件打包", [
            ("collect", "附件收集", 14), ("index", "附件目录", 13),
            ("hash", "完整性指纹", 12), ("zip", "打包导出", 15, False),
        ]),
        ("watermark_pdf", "PDF水印Agent", "导出 PDF 加水印", [
            ("pdf", "PDF 生成", 16), ("mark", "水印叠加", 14),
            ("perm", "权限设置", 10), ("export", "文件导出", 13, False),
        ]),
        ("docx_export", "Word导出Agent", "DOCX 终稿导出", [
            ("docx", "DOCX 生成", 17), ("style", "样式套用", 13),
            ("toc", "目录生成", 11), ("export", "文件导出", 14, False),
        ]),
        ("html_portal", "HTML门户Agent", "只读 HTML 报告页", [
            ("html", "页面生成", 15), ("nav", "章节导航", 12),
            ("search", "页内检索", 10), ("export", "静态导出", 13, False),
        ]),
        ("api_webhook", "报告WebhookAgent", "成稿推送外部系统", [
            ("hook", "Webhook 推送", 14), ("retry", "失败重试", 11),
            ("sign", "签名校验", 10), ("log", "投递日志", 9, False),
        ]),
        ("kpi_dashboard", "写报KPI看板Agent", "写报过程指标看板", [
            ("kpi", "指标汇总", 13), ("chart", "图表生成", 12),
            ("alert", "异常告警", 9), ("export", "看板导出", 10, False),
        ]),
        ("cost_meter", "模型成本计量Agent", "Token/费用统计", [
            ("token", "Token 统计", 14), ("cost", "费用估算", 13),
            ("budget", "预算告警", 10), ("export", "账单导出", 11, False),
        ]),
        ("prompt_library", "提示词库Agent", "写报提示词版本管理", [
            ("lib", "提示词库", 12), ("version", "版本管理", 11),
            ("ab", "A/B 试验", 9), ("export", "清单导出", 8, False),
        ]),
        ("eval_suite", "写报评测Agent", "成稿自动评测集", [
            ("rubric", "评分量表", 13), ("score", "自动打分", 12),
            ("regress", "回归对比", 10), ("export", "评测报告", 11, False),
        ]),
        ("human_loop", "人机协同审稿Agent", "人工批注回流", [
            ("comment", "批注采集", 12), ("resolve", "意见闭环", 11),
            ("assign", "审稿指派", 9), ("export", "批注导出", 8, False),
        ]),
    ],
}


def _pad_specs_for(cat_id: str, need: int, used_names: set) -> List[Tuple]:
    """取 need 条不与已有 name 冲突的补齐规格；不足则加场景后缀循环。"""
    pool = list(_PAD_SPECS.get(cat_id, []))
    picked: List[Tuple] = []
    # 第一轮：原名
    for spec in pool:
        if len(picked) >= need:
            break
        name = spec[1]
        if name in used_names:
            continue
        picked.append(spec)
        used_names.add(name)
    # 第二轮：名称加场景后缀，保证够数且仍像真实产品名
    scenes = ["增强版", "专项版", "深采版", "批量版", "情报版", "链路版", "归档版"]
    round_i = 0
    while len(picked) < need:
        if not pool:
            break
        base = pool[len(picked) % len(pool)]
        slug, name, desc, caps_list = base[0], base[1], base[2], base[3]
        scene = scenes[round_i % len(scenes)]
        round_i += 1
        new_name = f"{name}-{scene}"
        if new_name in used_names:
            new_name = f"{name}-{scene}{round_i}"
        if new_name in used_names:
            continue
        used_names.add(new_name)
        picked.append((f"{slug}_r{round_i}", new_name, f"{desc}（{scene}）", caps_list))
    return picked[:need]


def pad_category_l3(docs: List[Dict[str, Any]]) -> None:
    """把各大类下 L3 补到 TARGET_L3；名称用真实风格平台/能力名，禁止「扩展Agent-N」。"""
    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for d in docs:
        by_parent.setdefault(d.get("parentId") or "", []).append(d)

    for cat_id, target in TARGET_L3.items():
        existing = [d for d in by_parent.get(cat_id, []) if d.get("level") == 3]
        need = target - len(existing)
        if need <= 0:
            continue
        used_names = {d.get("name") for d in existing}
        used_ids = {d.get("id") for d in existing}
        specs = _pad_specs_for(cat_id, need, used_names)
        if len(specs) < need:
            raise RuntimeError(f"{cat_id} 补齐名池不足: need={need} got={len(specs)}")
        for i, spec in enumerate(specs, start=1):
            slug, name, desc, cap_items = spec[0], spec[1], spec[2], spec[3]
            aid = f"agent_{slug}"
            # 防 id 碰撞
            if aid in used_ids:
                aid = f"agent_{slug}_{i:02d}"
            used_ids.add(aid)
            show = i <= max(3, need // 4)
            w = max(5, 22 - (i % 18))
            docs.append(
                agent(
                    aid,
                    cat_id,
                    name,
                    description=desc,
                    show=show,
                    weight=w,
                    display_count=2,
                    last_active=f"{(i % 30) + 1} 分钟前",
                    version="v1.0",
                )
            )
            # L4 账号数略微扰动，避免完全相同
            varied = []
            for t in cap_items:
                suffix, cname, acc = t[0], t[1], t[2]
                show_c = t[3] if len(t) > 3 else True
                varied.append((suffix, cname, acc + (i % 5) * 8, show_c))
            docs.extend(caps(aid, varied, base_weight=80))


def recompute(docs: List[Dict[str, Any]]) -> None:
    """自底向上回写统计。

    - stats.childCount = 直接子节点数
    - badgeCount：L2/L3 = 直接子节点数；L4 = 装饰角标
    - allCount = 全部子孙节点数（不含自身）
    """
    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    by_id = {d["id"]: d for d in docs}
    for d in docs:
        by_parent.setdefault(d.get("parentId") or "", []).append(d)

    # L4
    for d in docs:
        if d["level"] == 4:
            d["stats"]["childCount"] = 0
            d["allCount"] = 0
            acc = int(d["stats"]["accountCount"] or 0)
            d["badgeCount"] = max(1, min(99, acc % 97 + 1)) if acc else 1

    # L3
    for d in docs:
        if d["level"] != 3:
            continue
        kids = by_parent.get(d["id"], [])
        d["stats"]["childCount"] = len(kids)
        d["stats"]["accountCount"] = sum(int(k["stats"]["accountCount"] or 0) for k in kids)
        d["badgeCount"] = len(kids)
        d["allCount"] = len(kids)
        d["stats"]["platformCount"] = max(1, int(d["stats"].get("platformCount") or 1))

    # L2：角标=直接 L3；allCount=L3+L4
    for d in docs:
        if d["level"] != 2:
            continue
        l3 = by_parent.get(d["id"], [])
        l4_n = 0
        acc = 0
        for a in l3:
            acc += int(a["stats"]["accountCount"] or 0)
            l4_n += int(a["stats"]["childCount"] or 0)
        d["stats"]["childCount"] = len(l3)
        d["stats"]["accountCount"] = acc
        d["stats"]["platformCount"] = max(1, len(l3))
        d["badgeCount"] = len(l3)
        d["allCount"] = len(l3) + l4_n

    # root：角标=全部 L3；allCount=root 以下全部节点
    root = by_id["root"]
    l2 = by_parent.get("root", [])
    root["stats"]["childCount"] = len(l2)
    root["stats"]["accountCount"] = sum(int(c["stats"]["accountCount"] or 0) for c in l2)
    l3_n = sum(1 for x in docs if x["level"] == 3)
    l4_n = sum(1 for x in docs if x["level"] == 4)
    root["badgeCount"] = l3_n
    root["allCount"] = len(l2) + l3_n + l4_n
    root["stats"]["platformCount"] = l3_n
    root["name"] = f"共计包含{l3_n}Agent"


def main() -> None:
    docs = build()
    recompute(docs)
    docs.sort(
        key=lambda d: (
            d["level"],
            d.get("parentId") or "",
            -int(d.get("displayConfig", {}).get("weight") or 0),
            d["id"],
        )
    )
    OUT.write_text(json.dumps(docs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    from collections import Counter

    c = Counter(d["level"] for d in docs)
    print("wrote", OUT, "total", len(docs), "levels", dict(c))
    for d in docs:
        if d["level"] <= 2:
            print(
                d["id"],
                d["name"],
                "badge",
                d["badgeCount"],
                "allCount",
                d.get("allCount"),
                "childCount",
                d["stats"]["childCount"],
            )
    # 抽样检查补齐命名
    bad = [d["name"] for d in docs if d["level"] == 3 and ("扩展Agent" in d["name"] or "假数据补齐" in d.get("description", ""))]
    print("bad_names", len(bad), bad[:5])
    sample = [d["name"] for d in docs if d["level"] == 3 and d["parentId"] == "cat_collect"][-5:]
    print("collect_tail_names", sample)


if __name__ == "__main__":
    main()
