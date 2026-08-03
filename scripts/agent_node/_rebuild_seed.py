# -*- coding: utf-8 -*-
"""一次性重建 agent_node_seed.json：扩充 L3/L4，并按实际子节点回写统计。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

OUT = Path(__file__).resolve().parent / "agent_node_seed.json"


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

    return docs


def recompute(docs: List[Dict[str, Any]]) -> None:
    """自底向上：childCount=直接子节点数；accountCount 父=子之和；badgeCount=childCount（L4 用自身 account 装饰角标保留原语义用 childCount=0 时 badge=原假数可改为 account 百位）。"""
    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    by_id = {d["id"]: d for d in docs}
    for d in docs:
        by_parent.setdefault(d.get("parentId") or "", []).append(d)

    # L4：childCount=0，badgeCount 用较小展示数（保持角标好看：accountCount 取模或固定）
    for d in docs:
        if d["level"] == 4:
            d["stats"]["childCount"] = 0
            # 角标：用 accountCount 的可读缩写感，夹在 1~99
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
        # platformCount 保持已有或至少 1
        d["stats"]["platformCount"] = max(1, int(d["stats"].get("platformCount") or 1))

    # L2：childCount=直接 L3 数；accountCount=子 L3 之和；badgeCount=下属全部节点数(L3+L4) 作「包含节点」
    for d in docs:
        if d["level"] != 2:
            continue
        l3 = by_parent.get(d["id"], [])
        l4_n = 0
        acc = 0
        plats = set()
        for a in l3:
            acc += int(a["stats"]["accountCount"] or 0)
            l4_n += int(a["stats"]["childCount"] or 0)
            plats.add(a["id"])
        d["stats"]["childCount"] = len(l3)
        d["stats"]["accountCount"] = acc
        d["stats"]["platformCount"] = max(1, len(l3))
        d["badgeCount"] = len(l3) + l4_n  # 包含节点 ≈ 三级+四级

    # root
    root = by_id["root"]
    l2 = by_parent.get("root", [])
    root["stats"]["childCount"] = len(l2)
    root["stats"]["accountCount"] = sum(int(c["stats"]["accountCount"] or 0) for c in l2)
    root["stats"]["platformCount"] = sum(int(c["stats"]["platformCount"] or 0) for c in l2)
    # 角标：全部 agent(L3) 数量，贴近「共计包含 N Agent」
    agent_n = sum(1 for d in docs if d["level"] == 3)
    root["badgeCount"] = agent_n
    root["name"] = f"共计包含{agent_n}Agent"


def main() -> None:
    docs = build()
    recompute(docs)
    # 稳定排序：level, parentId, -weight, id
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
        if d["level"] == 2:
            print(
                d["id"],
                d["name"],
                "badge",
                d["badgeCount"],
                "childCount",
                d["stats"]["childCount"],
                "accountCount",
                d["stats"]["accountCount"],
            )
    print("root", docs[0]["name"], "accountCount", docs[0]["stats"]["accountCount"])


if __name__ == "__main__":
    main()
