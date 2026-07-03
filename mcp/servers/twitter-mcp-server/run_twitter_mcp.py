#!/usr/bin/env python3
"""Hermes 启动器：为 twikit-mcp 注入显式 HTTP 代理，并扩展舆情画像工具。"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from twitter_mcp._vendor.twikit import Client
from twitter_mcp._vendor.twikit.errors import TwitterException


def _configure_windows_stdio_utf8() -> None:
    """Windows 下避免 MCP 子进程 stderr/stdout 中文或框线字符触发 GBK 解码错误。"""
    if os.name != "nt":
        return
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf:
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


_configure_windows_stdio_utf8()

# 分页与补样参数（避免触发 X API 限流）
_PAGE_SIZE = 40
_PAGE_DELAY_SEC = 2.0
_MAX_TIMELINE_PAGES = 8
_MAX_REPLY_PAGES = 2

# 议题关键词规则（画像观点聚类用，按优先级匹配）
VIEWPOINT_RULES: list[tuple[str, list[str]]] = [
    (
        "2019香港反修例运动",
        [
            "反送中",
            "逃犯條例",
            "逃犯条例",
            "反修例",
            "送中",
            "612",
            "6.12",
            "6月12",
            "721",
            "831",
            "梁凌杰",
            "林鄭",
            "林郑",
            "freehk",
            "光時",
            "光时",
            "五大訴求",
            "五大诉求",
            "时代革命",
            "時代革命",
            "聽香港",
            "二百萬",
            "200萬",
        ],
    ),
    (
        "香港現狀與法治民生",
        [
            "宏福苑",
            "李家超",
            "立法會",
            "立法会",
            "国安",
            "國安",
            "港警",
            "罪成",
            "判刑",
            "人大",
            "香港民生",
            "房屋",
        ],
    ),
    (
        "中國內地與政治社會",
        [
            "文革",
            "紅衛兵",
            "白纸",
            "白紙",
            "習近",
            "习近平",
            "共产党",
            "共產黨",
            "統戰",
            "统战",
            "跨省",
            "上访",
            "上訪",
            "鬥地主",
            "斗地主",
        ],
    ),
    (
        "兩岸關係",
        [
            "台灣",
            "台湾",
            "台海",
            "国军",
            "國軍",
            "共軍",
            "赖清德",
            "賴清德",
            "蔡英文",
            "武统",
            "武統",
            "两岸",
            "兩岸",
        ],
    ),
    (
        "國際局勢",
        [
            "烏克蘭",
            "乌克兰",
            "拜登",
            "Trump",
            "普京",
            "NATO",
            "以巴",
            "以色列",
            "伊朗",
            "Russia",
            "俄羅斯",
        ],
    ),
    (
        "經濟與社會議題",
        ["經濟", "经济", "楼市", "樓市", "失业", "失業", "通脹", "通胀", "股市", "房價"],
    ),
    (
        "歷史與紀念",
        ["歷史", "历史", "缅怀", "緬懷", "勿忘", "紀念", "纪念", "七年", "周年"],
    ),
]


def _resolve_proxy() -> str | None:
    for key in ("TWITTER_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def _persona_max_tweets() -> int:
    raw = (os.environ.get("TWITTER_PERSONA_MAX_TWEETS") or "280").strip()
    try:
        return max(60, min(int(raw), 320))
    except ValueError:
        return 280


def _persona_lookback_days() -> int:
    raw = (os.environ.get("TWITTER_PERSONA_LOOKBACK_DAYS") or "90").strip()
    try:
        return max(30, min(int(raw), 180))
    except ValueError:
        return 90


def _extract_rt_source(text: str) -> str | None:
    m = re.match(r"^RT @(\w+):", text or "")
    return m.group(1) if m else None


def _is_link_only(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return True
    without_urls = re.sub(r"https?://\S+", "", stripped).strip()
    return len(without_urls) < 5


def _tweet_to_persona_dict(t) -> dict[str, Any]:
    """推文结构化字段，供深度人物画像分析。"""
    try:
        text = t.full_text or t.text or ""
    except Exception:
        text = getattr(t, "text", "") or ""

    rt_obj = None
    try:
        rt_obj = getattr(t, "retweeted_tweet", None)
    except Exception:
        rt_obj = None

    is_retweet = bool(rt_obj) or text.startswith("RT @")
    is_reply = bool(getattr(t, "in_reply_to", None))
    is_quote = bool(getattr(t, "is_quote_status", False))
    if is_retweet:
        post_type = "retweet"
    elif is_reply:
        post_type = "reply"
    elif is_quote:
        post_type = "quote"
    else:
        post_type = "original"

    rt_source = None
    rt_text = ""
    if rt_obj:
        try:
            rt_text = rt_obj.full_text or rt_obj.text or ""
            if getattr(rt_obj, "user", None):
                rt_source = getattr(rt_obj.user, "screen_name", None)
        except Exception:
            pass
    if not rt_source:
        rt_source = _extract_rt_source(text)

    quoted_text = None
    quoted_author = None
    try:
        quote_obj = getattr(t, "quote", None)
        if quote_obj:
            quoted_text = (quote_obj.full_text or quote_obj.text or "")[:300]
            if getattr(quote_obj, "user", None):
                quoted_author = getattr(quote_obj.user, "screen_name", None)
    except Exception:
        pass

    place = getattr(t, "_place_data", None) or getattr(t, "place", None)
    place_name = None
    if isinstance(place, dict):
        place_name = place.get("full_name") or place.get("name")

    hashtags: list[str] = []
    try:
        hashtags = list(getattr(t, "hashtags", []) or [])
    except Exception:
        pass

    media_types: list[str] = []
    media_urls: list[str] = []
    media = getattr(t, "media", None)
    if media:
        for m in media:
            mtype = getattr(m, "type", None) or (
                m.get("type") if isinstance(m, dict) else None
            )
            if mtype:
                media_types.append(str(mtype))
            # 仅图片进 OCR 队列（跳过 video / animated_gif 首帧）
            if mtype and str(mtype).lower() in ("video", "animated_gif"):
                continue
            url = _media_url_from_item(m)
            if url:
                media_urls.append(url)

    return {
        "id": t.id,
        "text": text,
        "created_at": str(t.created_at),
        "lang": getattr(t, "lang", "") or "",
        "post_type": post_type,
        "in_reply_to": getattr(t, "in_reply_to", None),
        "likes": t.favorite_count,
        "retweets": t.retweet_count,
        "replies": getattr(t, "reply_count", 0),
        "quotes": getattr(t, "quote_count", 0) or 0,
        "views": getattr(t, "view_count", None),
        "place": place_name,
        "retweet_source": rt_source,
        "retweeted_text": rt_text[:500] if rt_text else None,
        "quoted_author": quoted_author,
        "quoted_text": quoted_text,
        "hashtags": hashtags,
        "is_link_only": _is_link_only(text) if post_type == "original" else False,
        "has_media": bool(media_types),
        "media_types": media_types,
        "media_urls": media_urls,
    }


def _media_url_from_item(m: Any) -> str | None:
    """从 twikit media 对象提取可 OCR 的图片 URL。"""
    if isinstance(m, dict):
        for key in ("media_url_https", "media_url", "url"):
            u = m.get(key)
            if u and str(u).startswith("http"):
                return str(u)
        return None
    for key in ("media_url_https", "media_url", "url"):
        u = getattr(m, key, None)
        if u and str(u).startswith("http"):
            return str(u)
    return None


def _profile_media_urls(user: Any) -> list[dict[str, str]]:
    """主页头像/背景图，供自动 OCR。"""
    out: list[dict[str, str]] = []
    avatar = getattr(user, "profile_image_url_https", None) or getattr(
        user, "profile_image_url", None
    )
    if avatar and str(avatar).startswith("http"):
        out.append({"source": "profile_avatar", "url": str(avatar)})
    banner = getattr(user, "profile_banner_url", None)
    if banner and str(banner).startswith("http"):
        out.append({"source": "profile_banner", "url": str(banner)})
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for item in out:
        if item["url"] not in seen:
            seen.add(item["url"])
            deduped.append(item)
    return deduped


def _build_media_for_ocr(
    rows: list[dict[str, Any]],
    user: Any,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """
    从推文样本与主页字段收集待 OCR 图片 URL。
    模型应在 Twitter 取证后自动调 mcp_ocr_perform_batch_ocr，无需用户手传图片。
    """
    items: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    def _add(source: str, url: str, tweet_id: str | None = None, likes: int = 0) -> None:
        if not url or url in seen_urls or len(items) >= limit:
            return
        seen_urls.add(url)
        entry: dict[str, Any] = {"source": source, "url": url, "likes": likes}
        if tweet_id:
            entry["tweet_id"] = tweet_id
        items.append(entry)

    for p in _profile_media_urls(user):
        _add(p["source"], p["url"])

    media_tweets = [r for r in rows if r.get("media_urls")]
    media_tweets.sort(
        key=lambda r: r.get("likes", 0) + r.get("retweets", 0) * 2,
        reverse=True,
    )
    for r in media_tweets:
        if len(items) >= limit:
            break
        for url in r.get("media_urls") or []:
            _add(
                "tweet_media",
                url,
                tweet_id=str(r.get("id", "")),
                likes=int(r.get("likes") or 0),
            )
            if len(items) >= limit:
                break

    return items


def _parse_twitter_dt(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
    except (ValueError, TypeError):
        return None


def _dt_to_date_str(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%d") if dt else None


def _hour_distribution(tweets: list[dict[str, Any]]) -> dict[str, int]:
    hours: Counter[int] = Counter()
    for tw in tweets:
        dt = _parse_twitter_dt(tw.get("created_at", ""))
        if dt:
            hours[dt.hour] += 1
    return {str(h): hours[h] for h in sorted(hours)}


def _monthly_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for r in rows:
        dt = _parse_twitter_dt(r.get("created_at", ""))
        if dt:
            counts[dt.strftime("%Y-%m")] += 1
    return dict(sorted(counts.items()))


def _extract_contacts(text: str) -> dict[str, list[str]]:
    emails = sorted(set(re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)))
    phones = sorted(set(re.findall(r"(?<!\d)(?:\+?86)?1[3-9]\d{9}(?!\d)", text)))
    urls = sorted(
        set(
            re.findall(
                r"https?://(?:t\.co/\w+|t\.me/\w+|github\.com/\S+|youtube\.com/\S+)",
                text,
            )
        )
    )
    return {"emails": emails, "phones": phones, "urls": urls}


def _extract_mentions(text: str) -> list[str]:
    return sorted(set(re.findall(r"@(\w+)", text or "")))


def _classify_viewpoint(row: dict[str, Any]) -> str:
    """按关键词将推文归入议题桶。"""
    parts = [
        row.get("text") or "",
        row.get("retweeted_text") or "",
        row.get("quoted_text") or "",
        " ".join(row.get("hashtags") or []),
    ]
    blob = " ".join(parts).lower()
    for theme, keywords in VIEWPOINT_RULES:
        for kw in keywords:
            if kw.lower() in blob:
                return theme
    return "其他議題"


def _infer_trend(monthly: dict[str, int]) -> str:
    if len(monthly) < 2:
        return "樣本不足"
    months = sorted(monthly.keys())
    mid = len(months) // 2
    first = sum(monthly[m] for m in months[:mid])
    second = sum(monthly[m] for m in months[mid:])
    if first == 0:
        return "近期新增"
    ratio = second / first
    if ratio >= 1.35:
        return "近期升溫"
    if ratio <= 0.65:
        return "近期降溫"
    return "持續活躍"


def _build_viewpoint_clusters(
    rows: list[dict[str, Any]], timeline_per_cluster: int = 30
) -> list[dict[str, Any]]:
    """按议题聚类，输出各观点下的推文时间线（按日期升序）。"""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        buckets[_classify_viewpoint(r)].append(r)

    total = len(rows) or 1
    clusters: list[dict[str, Any]] = []
    for theme, items in sorted(buckets.items(), key=lambda x: len(x[1]), reverse=True):
        dated: list[tuple[datetime, dict[str, Any]]] = []
        for r in items:
            dt = _parse_twitter_dt(r.get("created_at", ""))
            if dt:
                dated.append((dt, r))
        dated.sort(key=lambda x: x[0])

        monthly = _monthly_counts(items)
        first_dt = dated[0][0] if dated else None
        last_dt = dated[-1][0] if dated else None

        type_breakdown = dict(Counter(r["post_type"] for r in items))
        rt_sources = Counter(
            r["retweet_source"]
            for r in items
            if r.get("post_type") == "retweet" and r.get("retweet_source")
        )

        timeline = []
        for dt, r in dated[-timeline_per_cluster:]:
            timeline.append(
                {
                    "date": _dt_to_date_str(dt),
                    "datetime": dt.isoformat(),
                    "post_type": r["post_type"],
                    "likes": r["likes"],
                    "retweets": r["retweets"],
                    "retweet_source": r.get("retweet_source"),
                    "text_preview": (r.get("text") or "")[:200],
                }
            )

        clusters.append(
            {
                "theme": theme,
                "tweet_count": len(items),
                "share_pct": round(len(items) / total * 100, 1),
                "date_range": {
                    "first": _dt_to_date_str(first_dt),
                    "last": _dt_to_date_str(last_dt),
                },
                "monthly_counts": monthly,
                "trend": _infer_trend(monthly),
                "post_type_breakdown": type_breakdown,
                "top_retweet_sources": dict(rt_sources.most_common(10)),
                "timeline": timeline,
            }
        )
    return clusters


def _build_author_viewpoint_clusters(
    rows: list[dict[str, Any]], timeline_per_cluster: int = 20
) -> list[dict[str, Any]]:
    """博主本人发声（原创/回复/引用）的观点聚类。"""
    voice = [r for r in rows if r.get("post_type") in {"original", "reply", "quote"}]
    return _build_viewpoint_clusters(voice, timeline_per_cluster)


def _build_viewpoint_theme_summary(
    rows: list[dict[str, Any]], samples_per_theme: int = 2
) -> list[dict[str, Any]]:
    """精简议题摘要：无 timeline，每议题若干条样例推文供第二节分议题。"""
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        buckets[_classify_viewpoint(r)].append(r)

    total = len(rows) or 1
    summaries: list[dict[str, Any]] = []
    for theme, items in sorted(buckets.items(), key=lambda x: len(x[1]), reverse=True):
        dated: list[tuple[datetime, dict[str, Any]]] = []
        for r in items:
            dt = _parse_twitter_dt(r.get("created_at", ""))
            if dt:
                dated.append((dt, r))
        dated.sort(key=lambda x: x[0])
        first_dt = dated[0][0] if dated else None
        last_dt = dated[-1][0] if dated else None

        items_sorted = sorted(
            items,
            key=lambda r: (
                r.get("likes", 0) + r.get("retweets", 0) * 2,
                len(r.get("text") or ""),
            ),
            reverse=True,
        )
        samples: list[dict[str, Any]] = []
        for r in items_sorted[:samples_per_theme]:
            dt = _parse_twitter_dt(r.get("created_at", ""))
            samples.append(
                {
                    "date": _dt_to_date_str(dt),
                    "likes": r.get("likes", 0),
                    "retweets": r.get("retweets", 0),
                    "post_type": r.get("post_type"),
                    "text": (r.get("text") or "")[:280],
                }
            )

        summaries.append(
            {
                "theme": theme,
                "tweet_count": len(items),
                "share_pct": round(len(items) / total * 100, 1),
                "date_range": {
                    "first": _dt_to_date_str(first_dt),
                    "last": _dt_to_date_str(last_dt),
                },
                "sample_tweets": samples,
            }
        )
    return summaries


def _slim_viewpoint_stats(viewpoint: dict[str, Any]) -> dict[str, Any]:
    """去掉与 tweet_evidence_pack 重复的 long_originals，保留统计字段。"""
    slim = dict(viewpoint)
    slim.pop("notable_long_originals", None)
    return slim


# 仅对特定 handle 生效的公开身份锚点；其它账号必须为 null，禁止套用
_PUBLIC_IDENTITY_ANCHORS: dict[str, dict[str, Any]] = {
    "whyyoutouzhele": {
        "display_name": "李老师不是你老师",
        "legal_name_cn": "李颖",
        "forbidden_name_cn": "李英",
        "note": "仅当 analysis_target_lock.screen_name 为 whyyoutouzhele 时可用",
    },
}

_PERSONA_WRITING_REQUIREMENTS = (
    "【Twitter 主平台专用】多平台路由见 HERMES.md；输出结构以 mandatory_output_contract 为准，违反即不合格。"
    "【成稿模式】取证全部完成前禁止写预分析或六节；取证阶段最多一句进度。"
    "【两段式铁律】成稿必须含两个 # 一级标题：先 # 结构化预分析（七大点+1.1～7.7），"
    "再 # 正式人物画像报告（六节固定标题），禁止合并为一份「深度人物画像报告」。"
    "禁止自创标题如「基础身份信息」「可视形象描述」「政治立场定位」「关键图片分析」「综合评估」；"
    "禁止 @handle 深度人物画像报告 或 深度人物画像报告：@handle 作总标题。"
    "禁止用 Markdown 表格替代第二节 10+ 条推文原文立证；禁止 emoji（含 ⚠️🔥🚨）。"
    "第二节须从 tweet_evidence_pack 逐条引用至少 10 条推文原文（date+likes+原文），每议题至少 2 条。"
    "OCR 与 maigret 分轮：先 OCR（若有），再 maigret；禁止与 get_user_tweets_for_persona 同轮。"
    "第三节据 maigret 返回的 timed_out / user_message / summary.accounts 如实写；"
    "timed_out=true 写超时，禁止写成「未发现账号」；found_count>0 必须列出平台。"
    "第五节只写人工核查方向，禁止写工具故障与样本局限清单。"
    "OCR 全文只进「图文转换内容（已融合进分析中）」节，禁止进「可视形象描述」或「关键图片分析」节。"
)

_DRAFT_OPENING_CORRECT = [
    "# 结构化预分析",
    "## 一、账号标识与资料画像",
    "1.1 …",
    "…",
    "# 正式人物画像报告",
    "## 一、人物基本信息",
]

_DRAFT_OPENING_WRONG = [
    "深度人物画像报告：@handle",
    "一、基础身份信息",
    "二、可视形象描述",
    "五、政治立场定位",
    "结构化预分析",  # 缺 # 也算违规
]

# 预分析七大点标题（须逐字，禁止自创「视觉与行为画像」等）
_PRE_ANALYSIS_H2_LOCKED: list[str] = [
    "## 一、账号标识与资料画像",
    "## 二、账号规模与影响力",
    "## 三、地域属性与时空规律",
    "## 四、跨平台关联与联络线索",
    "## 五、内容议题与表达风格",
    "## 六、运营节奏与行为模式",
    "## 七、身份线索与关系网络",
]

_FORBIDDEN_PRE_ANALYSIS_H2: list[str] = [
    "## 二、视觉与行为画像",
    "## 三、跨平台足迹",
    "## 四、立场与倾向分析",
    "## 五、关键叙事与时间线",
    "## 六、社交关系与网络",
    "## 七、风险与操控评估",
    "## 二、视觉形象",
    "## 三、跨平台分析",
]

# 正式报告六节标题（须逐字）
_FORMAL_REPORT_H2_LOCKED: list[str] = [
    "## 一、人物基本信息",
    "## 二、发文观点总结与立证",
    "## 三、其他平台账号与发文分析",
    "## 四、真实人物画像推断",
    "## 五、核查思路",
    "## 六、人物深度报告画像",
]

_FORBIDDEN_FORMAL_REPORT_H2: list[str] = [
    "## 二、主要人物观点与发文记录",
    "## 二、主要人物观点",
    "## 三、其他平台信息",
    "## 四、社交影响力与互动模式",
    "## 五、社会关系与组织联系",
    "## 六、视觉风与个人审美",
    "## 四、影响力分析",
    "## 五、关系网络",
]


def _build_pre_analysis_outline() -> list[dict[str, Any]]:
    """预分析骨架：模型须按此七大点+编号填空，禁止改章节名。"""
    specs = [
        ("## 一、账号标识与资料画像", ["1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7"]),
        ("## 二、账号规模与影响力", ["2.1", "2.2", "2.3", "2.4", "2.5"]),
        ("## 三、地域属性与时空规律", ["3.1", "3.2", "3.3", "3.4", "3.5", "3.6", "3.7"]),
        ("## 四、跨平台关联与联络线索", ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7", "4.8", "4.9", "4.10", "4.11"]),
        ("## 五、内容议题与表达风格", ["5.1", "5.2", "5.3", "5.4", "5.5", "5.6", "5.7", "5.8", "5.9"]),
        ("## 六、运营节奏与行为模式", ["6.1", "6.2", "6.3", "6.4", "6.5", "6.6", "6.7"]),
        ("## 七、身份线索与关系网络", ["7.1", "7.2", "7.3", "7.4", "7.5", "7.6", "7.7"]),
    ]
    return [
        {
            "h2_must_be_exactly": h2,
            "subpoint_ids": subs,
            "rule": "每个编号单独一行作答；无数据写「无数据」，禁止跳过或合并成大段无编号散文",
        }
        for h2, subs in specs
    ]


def _format_evidence_citation_line(e: dict[str, Any]) -> str:
    prefix = ""
    if e.get("post_type") == "retweet" and e.get("retweet_source"):
        prefix = f"RT @{e['retweet_source']}: "
    text = ((e.get("text") or "").replace("\n", " ").strip())[:480]
    return (
        f"{e.get('date', '')}｜赞{e.get('likes', 0)}｜"
        f"转{e.get('retweets', 0)}｜「{prefix}{text}」"
    )


def _build_section2_citation_template(
    evidence_pack: list[dict[str, Any]],
) -> dict[str, Any]:
    """第二节立证：预格式化行，要求模型逐条粘贴进「二、发文观点总结与立证」。"""
    lines = [_format_evidence_citation_line(e) for e in evidence_pack[:14]]
    by_theme: dict[str, list[str]] = {}
    for e in evidence_pack[:14]:
        theme = str(e.get("theme") or "其他議題")
        by_theme.setdefault(theme, []).append(_format_evidence_citation_line(e))
    return {
        "required_h2_exactly": "## 二、发文观点总结与立证",
        "forbidden_h2": _FORBIDDEN_FORMAL_REPORT_H2[:2],
        "min_citation_lines": 10,
        "min_section_chars": 1200,
        "format_rule": (
            "按 viewpoint_theme_summary 分议题；每议题下先逐条粘贴 citation_lines，"
            "再写观点归纳。禁止用 1.2.3. 编号列表代替「日期｜赞｜转｜「原文」」格式。"
        ),
        "citation_lines_copy_into_section2": lines,
        "citation_lines_by_theme": by_theme,
    }


def _build_mandatory_output_contract(*, has_ocr: bool) -> dict[str, Any]:
    """成稿时必须遵循的标题骨架；模型不得改标题或跳过预分析。"""
    pre_analysis_heads = [
        "# 结构化预分析",
        "## 一、账号标识与资料画像",
        "## 二、账号规模与影响力",
        "## 三、地域属性与时空规律",
        "## 四、跨平台关联与联络线索",
        "## 五、内容议题与表达风格",
        "## 六、运营节奏与行为模式",
        "## 七、身份线索与关系网络",
        "# 正式人物画像报告",
        "## 一、人物基本信息",
        "## 二、发文观点总结与立证",
        "## 三、其他平台账号与发文分析",
        "## 四、真实人物画像推断",
        "## 五、核查思路",
        "## 六、人物深度报告画像",
    ]
    contract: dict[str, Any] = {
        "structure_mode": "TWO_H1_PARTS_REQUIRED",
        "single_response_order": pre_analysis_heads,
        "draft_first_line_must_be": "# 结构化预分析",
        "draft_second_h1_must_be": "# 正式人物画像报告",
        "draft_ends_at": "## 六、人物深度报告画像",
        "correct_opening_lines": _DRAFT_OPENING_CORRECT,
        "wrong_opening_lines": _DRAFT_OPENING_WRONG,
        "ocr_section_required": has_ocr,
        "ocr_section_rule": (
            "仅当已调用 mcp_ocr_perform_batch_ocr 且至少一张图有非空文字时，"
            "在六节之后追加「图文转换内容（已融合进分析中）」。"
            if has_ocr
            else "media_for_ocr 为空：禁止调用 OCR；禁止出现「图文转换内容」节；"
            "禁止写「无OCR数据」等占位说明。"
        ),
        "pre_analysis_rule": (
            "预分析七大点 h2 标题必须逐字使用 pre_analysis_h2_locked；"
            "每个大点下按 subpoint_ids 编号输出（1.1～7.7）；"
            "无数据写「无数据」，禁止跳过编号。"
            "禁止用「视觉与行为画像」「跨平台足迹」「立场与倾向分析」等自创章节。"
        ),
        "pre_analysis_h2_locked": _PRE_ANALYSIS_H2_LOCKED,
        "forbidden_pre_analysis_h2": _FORBIDDEN_PRE_ANALYSIS_H2,
        "pre_analysis_outline": _build_pre_analysis_outline(),
        "formal_report_h2_locked": _FORMAL_REPORT_H2_LOCKED,
        "forbidden_formal_report_h2": _FORBIDDEN_FORMAL_REPORT_H2,
        "section2_rule": (
            "二、发文观点总结与立证：标题须逐字，禁止「主要人物观点与发文记录」。"
            "≥1200字，≥10条：从 section2_citation_template.citation_lines_copy_into_section2 "
            "逐条粘贴，格式已为「日期｜赞｜转｜「原文」」；按议题分组后写归纳。"
        ),
        "section5_rule": (
            "五、核查思路：公文式段落，按正式报告第一～四节分块（「关于账号…」「关于发文立证…」）。"
            "## 五、核查思路 下一行直接写实质内容，禁止开篇套话："
            "禁止「本节面向核查方/甲方」「证据来源与推理路径」「关于第五节本身」等元话语。"
            "只追溯正文已有结论；禁止表格、工具名、下一步调查清单。"
        ),
        "forbidden_titles": [
            "一、基础信息",
            "一、基础身份信息",
            "二、视觉与行为画像",
            "二、可视形象描述",
            "二、主要人物观点与发文记录",
            "二、政治立场",
            "三、跨平台足迹",
            "三、跨平台关联分析",
            "三、其他平台信息",
            "三、主要议题分布",
            "四、立场与倾向分析",
            "四、信息来源与转发网络",
            "四、社交影响力与互动模式",
            "五、关键叙事与时间线",
            "五、关键图片分析",
            "五、政治立场定位",
            "五、社会关系与组织联系",
            "六、社交关系与网络",
            "六、综合评估",
            "六、视觉风与个人审美",
            "六、图文转换内容（已融合进以上分析）",
            "七、风险与操控评估",
            "深度人物画像报告",
            "深度人物画像报告：@",
            "@{screen_name} 深度人物画像报告",
            "重要局限性声明",
        ],
        "forbidden_formats": [
            "跳过「# 结构化预分析」直接写报告",
            "写「结构化预分析」但缺少行首 # 号",
            "只有一个总标题「深度人物画像报告」而无两个 # 一级标题",
            "预分析用自创七大点（视觉与行为/跨平台足迹/立场与倾向等）",
            "正式报告用自创六节（社交影响力/视觉风/主要人物观点等）",
            "预分析与正式报告混写在一份自创结构里",
            "第二节用 1.2.3. 编号列表代替「日期｜赞｜转｜原文」",
            "用 Markdown 表格充当第二节立证",
            "正文 emoji",
            "把 OCR 全文放在二～六节或「可视形象描述」而非「图文转换内容」节",
            "第五节开篇写本节面向核查方/甲方或证据来源与推理路径",
            "第五节写关于第五节本身的自指段落",
            "第五节用Markdown表格代替段落话术",
            "第五节出现vision_analyze/Apify/maigret/MCP等工具函数名",
            "第五节写 maigret/OCR/MCP 失败细节",
            "第五节写下一步调查建议",
            "第五节编造正文未出现的事实",
            "文末写「重要局限性声明」技术清单",
            "正式报告缺少「五、核查思路」或「六、人物深度报告画像」",
        ],
    }
    if not has_ocr:
        contract["forbidden_tail_sections"] = [
            "图文转换内容（已融合进分析中）",
            "无OCR数据",
        ]
        contract["draft_ends_at_rule"] = (
            "成稿必须以「## 六、人物深度报告画像」正文结束；"
            "其后禁止出现任何章节、分隔线或「无OCR数据」说明。"
        )
    else:
        pre_analysis_heads.append("## 图文转换内容（已融合进分析中）")
        contract["single_response_order"] = pre_analysis_heads
    return contract


def _build_compliance_gate(*, has_ocr: bool) -> dict[str, Any]:
    """置于 persona 返回体最前：禁止收到本工具后立刻成稿。"""
    pending: list[str] = []
    if has_ocr:
        pending.append("mcp_ocr_perform_batch_ocr（media_for_ocr 全部 url，chi_sim）")
        pending.append(
            "vision_analyze(image_url=media_for_ocr 每项, question=人物/文字/场景；URL 失败再用 base64 重试)"
        )
    pending.append("mcp_maigret_collect_accounts（top_sites=10, timeout=150, primary_platform=twitter, primary_username=本次 screen_name）")
    gate: dict[str, Any] = {
        "status": "DATA_COLLECTED_NOT_READY_TO_DRAFT",
        "message": (
            "Twitter 主平台数据已返回；禁止立即写「用户画像报告」或任何预分析/六节正文。"
            "须先完成下列工具，再按 mandatory_output_contract 单次成稿。"
        ),
        "pending_tools_before_draft": pending,
        "draft_first_line_must_be": "# 结构化预分析",
        "draft_second_h1_must_be": "# 正式人物画像报告",
        "draft_forbidden_first_lines": [
            "深度人物画像报告",
            "深度人物画像报告：",
            "用户画像报告",
            "一、基础信息",
            "一、基础身份信息",
            "二、发布类型",
            "二、政治立场",
            "二、可视形象描述",
            "用户画像报告：@",
            "I'll start by",
            "正在撰写",
        ],
        "draft_forbidden_until_all_pending_tools_called": True,
    }
    if has_ocr:
        gate["mandatory_builtin_tools_before_maigret"] = ["vision_analyze"]
        gate["forbid_maigret_until_vision_analyze"] = True
        gate["vision_analyze_rule"] = (
            "内置工具 vision_analyze（非 mcp_ 前缀）；OCR 后、maigret 前必调；"
            "日志须出现 preparing vision_analyze"
        )
        gate["vision_independent_of_ocr"] = (
            "OCR 无文字/识别差/返回空 均不得跳过 vision_analyze；"
            "只要 media_for_ocr 非空就必须调多模态"
        )
        gate["forbidden_skip_vision_reasons"] = [
            "OCR未识别出有效文字",
            "OCR无文字所以跳过vision",
            "头像无可读文字",
        ]
        gate["forbidden_tools_during_pending"] = [
            "browser_console",
            "browser_console_messages",
            "browser_vision",
            "browser_navigate",
            "browser_snapshot",
            "mcp_playwright_browser_navigate",
            "mcp_playwright_browser_take_screenshot",
            "mcp_playwright_browser_console_messages",
        ]
        gate["vision_not_browser_rule"] = (
            "禁止用 browser_* / console 代替 vision_analyze；"
            "须直接调工具 vision_analyze(image_url, question)"
        )
    if not has_ocr:
        gate["ocr_section"] = "OMIT"
        gate["ocr_instruction"] = (
            "media_for_ocr 为空：禁止调用 OCR；成稿时禁止出现「图文转换内容（已融合进分析中）」"
            "及任何「无OCR数据」占位文字。"
        )
    return gate


def _persona_tool_result(srv: Any, payload: dict[str, Any]) -> str:
    """工具返回：明文门禁 + JSON，降低模型忽略契约的概率。"""
    gate = payload.get("compliance_gate") or {}
    pending = gate.get("pending_tools_before_draft") or []
    pending_txt = " → ".join(pending) if pending else "maigret"
    ocr_line = ""
    if gate.get("ocr_section") == "OMIT":
        ocr_line = (
            "无图片：禁止 OCR；成稿以「六、人物深度报告画像」结束，"
            "禁止写「图文转换内容」节或「无OCR数据」占位\n"
        )
    else:
        ocr_line = (
            "有图时：OCR → vision_analyze（内置，非 mcp_）→ maigret；"
            "OCR 无文字也禁止跳过 vision；未完成 vision 禁止 maigret\n"
            "vision_analyze(image_url=media_for_ocr[0], question=人物/文字/场景)\n"
            "OCR 全文只进「图文转换内容（已融合进分析中）」节\n"
        )
    vision_banner = ""
    if gate.get("forbid_maigret_until_vision_analyze"):
        vision_banner = (
            "【必调多模态】有图必调 vision_analyze；OCR 无文字不得跳过\n"
            "OCR 后必须调 vision_analyze；日志须出现 preparing vision_analyze\n"
            "禁止：browser_console / browser_* / mcp_playwright_* 代替 vision_analyze\n"
        )
    banner = (
        "【画像合规门禁 / 禁止立即成稿】\n"
        f"{vision_banner}"
        "成稿第一行：# 结构化预分析（必须有#号）\n"
        "预分析七大点标题固定：账号标识与资料画像→账号规模与影响力→地域属性与时空规律→"
        "跨平台关联与联络线索→内容议题与表达风格→运营节奏与行为模式→身份线索与关系网络\n"
        "正式报告六节固定：人物基本信息→发文观点总结与立证→其他平台账号与发文分析→"
        "真实人物画像推断→核查思路（标题下直接写关于××分块，禁套话）→人物深度报告画像\n"
        "第二节：从 section2_citation_template 逐条粘贴「日期｜赞｜转｜原文」，禁止「主要人物观点与发文记录」\n"
        "画像采集请用 get_user_tweets_for_persona（已含 user_profile），禁止 get_user_info。\n"
        f"未完成前禁止写报告正文。取证后续：{pending_txt}\n"
        f"成稿第一行必须是：{gate.get('draft_first_line_must_be', '# 结构化预分析')}\n"
        f"成稿第二部分必须以：{gate.get('draft_second_h1_must_be', '# 正式人物画像报告')} 开头\n"
        f"{ocr_line}"
        "禁止预分析章节：视觉与行为画像/跨平台足迹/立场与倾向分析/风险与操控评估\n"
        "---JSON_BELOW---\n"
    )
    return banner + srv._dumps(payload)


def _build_analysis_target_lock(screen_name: str, user: Any) -> dict[str, Any]:
    """同会话换人分析时，强制模型锁定本次目标，防止串写上一轮人物。"""
    sn = (screen_name or "").lstrip("@").lower()
    display = getattr(user, "name", "") or ""
    desc = (getattr(user, "description", "") or "")[:300]
    return {
        "screen_name": sn,
        "display_name": display,
        "user_id": str(getattr(user, "id", "")),
        "description_preview": desc,
        "followers_count": getattr(user, "followers_count", None),
        "statuses_count": getattr(user, "statuses_count", None),
        "session_isolation_rules": [
            f"本返回体仅服务于 @{sn}，与同会话其它人物画像严格隔离",
            f"报告标题与第一节主语必须是 @{sn}（显示名：{display or '见工具'}）",
            "禁止把上一轮其它 @handle 的姓名、推文摘录、立场、维基段落写入本次报告",
            "第二节立证只能引用本次 tweet_evidence_pack，禁止复述历史回复里的推文",
            "web_search 若出现其它账号名，不得当作本次主体（除非同人核查且写明依据）",
        ],
        "public_identity_anchor": _PUBLIC_IDENTITY_ANCHORS.get(sn),
        "mandatory_report_sections": [
            "一、人物基本信息",
            "二、发文观点总结与立证",
            "三、其他平台账号与发文分析",
            "四、真实人物画像推断",
            "五、核查思路",
            "六、人物深度报告画像",
        ],
        "optional_report_sections_if_ocr": [
            "图文转换内容（已融合进分析中）",
        ],
        "reference_files": {
            "contracts_readme": "contracts/README.md",
            "pre_analysis_dimensions": "contracts/pre-analysis-dimensions.md",
            "formal_report_template": "contracts/formal-report-template.md",
            "evidence_mining_checklist": "contracts/evidence-mining-checklist.md",
            "public_identities": "contracts/platforms/twitter/public-identities.md",
            "tweet_evidence_rules": "contracts/platforms/twitter/tweet-evidence-rules.md",
        },
        "forbidden_report_patterns": [
            "禁止 emoji（含 ⚠️🔥🚨）",
            "禁止自创章节：基础身份信息/可视形象描述/政治立场定位/议题分布/图片分析/综合评估",
            "禁止总标题「深度人物画像报告」或「@handle 深度人物画像报告」；须用 # 结构化预分析 与 # 正式人物画像报告 两段",
            "禁止跳过「# 结构化预分析」七大点",
            "禁止把 maigret 结果放在第二节；第二节只能是 tweet_evidence_pack 推文立证",
            "禁止用 Markdown 表格替代第二节 10+ 条推文原文",
            "禁止用三四节合并替代六节",
            "禁止分段成稿：先输出一二节、停住、再调 maigret",
            "禁止 OCR 与 maigret 同轮调用",
            "禁止向用户索取本地图片路径；有 media_for_ocr 时必须自动调 OCR",
            "有 OCR 结果时六节后必须追加「图文转换内容（已融合进分析中）」；OCR 不得单独成「关键图片分析」节",
            "media_for_ocr 为空时禁止写「图文转换内容」节及「无OCR数据」占位",
            "第三节/第五节禁止写 worker exit、Connection closed 等技术栈细节",
            "maigret：timed_out=true 写超时；found_count>0 列 summary.accounts；timed_out=false 且零命中写未发现账号",
            "画像禁止反复调用 get_user_info；须用 get_user_tweets_for_persona（含主页+推文）",
        ],
        "data_collection_sequence": [
            "1. get_user_tweets_for_persona（单独一轮；已含主页字段，勿再调 get_user_info）",
            "2. 若 media_for_ocr 非空：mcp_ocr_perform_batch_ocr（单独一轮，禁止与 maigret 同轮）",
            "3. maigret collect_accounts (top_sites=10, timeout=150)（单独一轮）",
            "4. 全部返回后按 mandatory_output_contract 单次成稿",
        ],
        "report_delivery_mode": "single_response_after_all_tools",
    }


def _build_user_profile(user: Any) -> dict[str, Any]:
    """主页字段（等价于 get_user_info），供画像第一轮一次拿齐。"""
    return {
        "id": str(getattr(user, "id", "")),
        "screen_name": getattr(user, "screen_name", ""),
        "name": getattr(user, "name", ""),
        "description": getattr(user, "description", "") or "",
        "created_at": str(getattr(user, "created_at", "")),
        "followers_count": getattr(user, "followers_count", None),
        "following_count": getattr(user, "following_count", None),
        "tweets_count": getattr(user, "statuses_count", None),
        "favourites_count": getattr(user, "favourites_count", None),
        "verified": getattr(user, "verified", None),
        "is_blue_verified": getattr(user, "is_blue_verified", None),
        "profile_image_url": getattr(user, "profile_image_url", None),
        "profile_banner_url": getattr(user, "profile_banner_url", None),
        "location": getattr(user, "location", None),
        "protected": getattr(user, "protected", None),
    }


def _assemble_slim_persona_payload(
    *,
    screen_name: str,
    all_rows: list[dict[str, Any]],
    timeline_rows: list[dict[str, Any]],
    reply_rows: list[dict[str, Any]],
    user: Any,
    fetch_warnings: list[str],
    coverage_summary: dict[str, Any],
    window_meta: dict[str, Any],
    tl_fetch: dict[str, Any],
    supplemented: bool,
    replies_meta: dict[str, Any],
    evidence_limit: int = 40,
) -> dict[str, Any]:
    """组装瘦身版画像 JSON：evidence 置前，去掉重复 timeline 字段。"""
    type_counts = Counter(r["post_type"] for r in all_rows)
    viewpoint = _slim_viewpoint_stats(_build_viewpoint_stats(all_rows))
    evidence_pack = _build_evidence_pack(all_rows, limit=evidence_limit)
    theme_summary = _build_viewpoint_theme_summary(all_rows, samples_per_theme=2)
    monthly_trends = _monthly_counts(timeline_rows)

    rt_ratio = (
        round(viewpoint["retweet_count"] / len(all_rows) * 100, 1) if all_rows else 0
    )
    voice_ratio = (
        round(viewpoint["author_voice_count"] / len(all_rows) * 100, 1)
        if all_rows
        else 0
    )

    sample_coverage = {
        **window_meta,
        **coverage_summary,
        "timeline_fetch": tl_fetch,
        "monthly_search_used": supplemented,
        "replies_fetch": replies_meta,
    }

    target_lock = _build_analysis_target_lock(screen_name, user)
    media_for_ocr = _build_media_for_ocr(all_rows, user, limit=8)
    output_contract = _build_mandatory_output_contract(has_ocr=bool(media_for_ocr))
    compliance_gate = _build_compliance_gate(has_ocr=bool(media_for_ocr))
    section2_template = _build_section2_citation_template(evidence_pack)

    # compliance_gate 必须第一：防止模型收到数据后立即自创章节成稿
    return {
        "compliance_gate": compliance_gate,
        "mandatory_output_contract": output_contract,
        "section2_citation_template": section2_template,
        "analysis_target_lock": target_lock,
        "user_profile": _build_user_profile(user),
        "report_writing_requirements": _PERSONA_WRITING_REQUIREMENTS,
        "tweet_evidence_pack": evidence_pack,
        "media_for_ocr": media_for_ocr,
        "ocr_pipeline": {
            "required": bool(media_for_ocr),
            "skip_when_empty": not bool(media_for_ocr),
            "tool": "mcp_ocr_perform_batch_ocr",
            "language": "chi_sim",
            "inputs_field": "media_for_ocr[].url",
            "instruction": (
                "media_for_ocr 非空时：Twitter 取证后自动 OCR；禁止向用户要本地图片。"
                "OCR 全文只进「图文转换内容（已融合进分析中）」节（六节之后）。"
                if media_for_ocr
                else "media_for_ocr 为空：禁止调用 OCR；禁止写「图文转换内容」节；"
                "禁止写「无OCR数据」占位。"
            ),
        },
        "sample_coverage": sample_coverage,
        "viewpoint_theme_summary": theme_summary,
        "screen_name": screen_name,
        "sample_size": len(all_rows),
        "timeline_count": len(timeline_rows),
        "replies_sample_count": len(reply_rows),
        "partial": bool(fetch_warnings) or coverage_summary.get("coverage_incomplete"),
        "fetch_warnings": fetch_warnings,
        "post_type_breakdown": dict(type_counts),
        "monthly_trends": monthly_trends,
        "viewpoint_stats": viewpoint,
        "content_mode_hint": (
            f"{coverage_summary.get('model_writing_hint', '')} "
            f"转发约 {rt_ratio}%，本人发声约 {voice_ratio}%。"
            "按 viewpoint_theme_summary 分议题写观点，用 tweet_evidence_pack 立证。"
        ),
        "account_created_at": str(user.created_at),
        "total_tweet_count": user.statuses_count,
    }


async def _fetch_tweets_paginated(
    client: Client,
    user_id: str,
    tweet_type: Literal["Tweets", "Replies", "Media", "Likes"],
    target_count: int,
) -> list:
    """分页拉取时间线，单次最多 100，自动翻页直到达到 target_count 或无更多。"""
    target_count = max(1, target_count)
    page_size = min(100, target_count)
    result = await client.get_user_tweets(user_id, tweet_type, count=page_size)
    collected: list = list(result)
    current = result
    while len(collected) < target_count and current.next_cursor:
        try:
            nxt = await current.next()
        except Exception:
            break
        if not nxt or len(nxt) == 0:
            break
        collected.extend(list(nxt))
        current = nxt
    return collected[:target_count]


def _oldest_tweet_dt(tweets: list) -> datetime | None:
    dts = [_parse_twitter_dt(str(t.created_at)) for t in tweets]
    dts = [d for d in dts if d]
    return min(dts) if dts else None


async def _fetch_timeline_until_lookback(
    client: Client,
    user_id: str,
    tweet_type: Literal["Tweets", "Replies", "Media", "Likes"],
    lookback_days: int,
    max_tweets: int,
    max_pages: int,
) -> tuple[list, dict[str, Any]]:
    """分页拉取直到触达 lookback 截止日、页数/条数上限或限流。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    collected: list = []
    errors: list[str] = []
    pages = 0
    reached_cutoff = False
    stopped_reason = "unknown"

    try:
        result = await client.get_user_tweets(user_id, tweet_type, count=_PAGE_SIZE)
        collected.extend(list(result))
        pages = 1
        current = result

        while pages < max_pages and len(collected) < max_tweets and current.next_cursor:
            oldest = _oldest_tweet_dt(collected)
            if oldest and oldest < cutoff:
                reached_cutoff = True
                stopped_reason = "reached_lookback_cutoff"
                break
            await asyncio.sleep(_PAGE_DELAY_SEC)
            try:
                nxt = await current.next()
            except TwitterException as exc:
                errors.append(f"page {pages + 1}: {exc}")
                stopped_reason = "rate_limit_or_error"
                break
            if not nxt or len(nxt) == 0:
                stopped_reason = "empty_page"
                break
            collected.extend(list(nxt))
            current = nxt
            pages += 1

        if stopped_reason == "unknown":
            if not current.next_cursor:
                stopped_reason = "no_more_cursor"
            elif pages >= max_pages:
                stopped_reason = "max_pages"
            elif len(collected) >= max_tweets:
                stopped_reason = "max_tweets"
    except TwitterException as exc:
        errors.append(f"first_page: {exc}")
        stopped_reason = "first_page_error"

    return collected[:max_tweets], {
        "pages_fetched": pages,
        "raw_fetched": len(collected),
        "reached_lookback_cutoff": reached_cutoff,
        "stopped_reason": stopped_reason,
        "errors": errors,
        "stopped_early": bool(errors),
    }


async def _fetch_timeline_pages(
    client: Client,
    user_id: str,
    tweet_type: Literal["Tweets", "Replies", "Media", "Likes"],
    max_pages: int,
    page_size: int = _PAGE_SIZE,
) -> tuple[list, dict[str, Any]]:
    """分页拉取时间线，带间隔与限流容错，失败时返回已采集部分。"""
    collected: list = []
    errors: list[str] = []
    pages = 0
    try:
        result = await client.get_user_tweets(user_id, tweet_type, count=page_size)
        batch = list(result)
        collected.extend(batch)
        pages = 1
        current = result
        while pages < max_pages and current.next_cursor:
            await asyncio.sleep(_PAGE_DELAY_SEC)
            try:
                nxt = await current.next()
            except TwitterException as exc:
                errors.append(f"page {pages + 1}: {exc}")
                break
            if not nxt or len(nxt) == 0:
                break
            collected.extend(list(nxt))
            current = nxt
            pages += 1
    except TwitterException as exc:
        errors.append(f"first_page: {exc}")

    return collected, {
        "pages_fetched": pages,
        "raw_fetched": len(collected),
        "errors": errors,
        "stopped_early": bool(errors),
    }


def _filter_by_lookback(
    tweets: list, lookback_days: int, reached_cutoff: bool = False
) -> tuple[list, dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    in_window: list = []
    for t in tweets:
        dt = _parse_twitter_dt(str(t.created_at))
        if dt and dt >= cutoff:
            in_window.append(t)

    oldest_in = None
    newest_in = None
    if in_window:
        dts = [_parse_twitter_dt(str(t.created_at)) for t in in_window]
        dts = [d for d in dts if d]
        if dts:
            oldest_in = min(dts)
            newest_in = max(dts)

    calendar_span = 0
    if oldest_in and newest_in:
        calendar_span = (newest_in.date() - oldest_in.date()).days + 1

    full_window = reached_cutoff or (
        oldest_in is not None and oldest_in <= cutoff + timedelta(days=2)
    )
    coverage_incomplete = not full_window and calendar_span < lookback_days

    return in_window, {
        "lookback_days_requested": lookback_days,
        "cutoff_date": cutoff.date().isoformat(),
        "oldest_in_sample": _dt_to_date_str(oldest_in),
        "newest_in_sample": _dt_to_date_str(newest_in),
        "actual_calendar_days_covered": calendar_span,
        "actual_span_days": calendar_span,
        "in_window_count": len(in_window),
        "full_window_covered": full_window,
        "coverage_incomplete": coverage_incomplete,
    }


def _month_ranges(lookback_days: int) -> list[tuple]:
    """按月分段，用于时间线未触达窗口时的搜索补样。"""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=lookback_days)
    ranges: list[tuple] = []
    year, month = start.year, start.month
    while True:
        cur = datetime(year, month, 1).date()
        if cur > today:
            break
        if month == 12:
            nxt = datetime(year + 1, 1, 1).date()
        else:
            nxt = datetime(year, month + 1, 1).date()
        end = min(nxt, today + timedelta(days=1))
        if end > start:
            ranges.append((max(cur, start), end))
        if nxt > today:
            break
        year, month = nxt.year, nxt.month
    return ranges


async def _fetch_monthly_search_samples(
    client: Client,
    screen_name: str,
    lookback_days: int,
    per_month: int = 20,
) -> tuple[list, dict[str, Any]]:
    """按月搜索补样，拉长高频号的时间覆盖。"""
    collected: list = []
    seen: set[str] = set()
    month_stats: dict[str, int] = {}
    errors: list[str] = []

    for start, end in _month_ranges(lookback_days):
        label = start.strftime("%Y-%m")
        q = f"from:{screen_name} since:{start} until:{end}"
        month_batch: list = []
        try:
            await asyncio.sleep(_PAGE_DELAY_SEC)
            result = await client.search_tweet(q, "Latest", count=20)
            month_batch.extend(list(result))
        except TwitterException as exc:
            errors.append(f"{label}: {exc}")
            continue

        added = 0
        for t in month_batch[:per_month]:
            tid = str(t.id)
            if tid not in seen:
                seen.add(tid)
                collected.append(t)
                added += 1
        month_stats[label] = added

    return collected, {
        "months_searched": len(_month_ranges(lookback_days)),
        "tweets_added": len(collected),
        "per_month_target": per_month,
        "monthly_hits": month_stats,
        "errors": errors,
    }


def _build_coverage_summary(
    lookback: int,
    sample_count: int,
    window_meta: dict[str, Any],
    tl_fetch: dict[str, Any],
    supplemented: bool,
    total_tweet_count: int | None = None,
) -> dict[str, Any]:
    """生成给模型看的样本说明，避免把「请求窗口」说成「实际覆盖」。"""
    cal_days = window_meta.get("actual_calendar_days_covered", 0)
    oldest = window_meta.get("oldest_in_sample") or "?"
    newest = window_meta.get("newest_in_sample") or "?"
    incomplete = window_meta.get("coverage_incomplete", True)
    reached = tl_fetch.get("reached_lookback_cutoff", False)

    est_daily = ""
    if total_tweet_count and cal_days > 0 and sample_count > 0:
        est_daily = f" 样本期内约每天 {round(sample_count / cal_days)} 条。"

    if reached and not incomplete:
        hint = (
            f"目标窗口近 {lookback} 天；时间线分页已触达截止日。"
            f"实际样本 {sample_count} 条，日历覆盖 {oldest} 至 {newest}（{cal_days} 天）。"
            f"{est_daily}"
        )
    elif incomplete:
        hint = (
            f"目标分析窗口：近 {lookback} 天（非实际覆盖天数）。"
            f"实际样本 {sample_count} 条，仅日历覆盖 {oldest} 至 {newest} 共 {cal_days} 天"
            f"（因发帖频率高或 API 分页/限流，未拉满 {lookback} 天）。"
            f"{est_daily}"
            f"画像开头必须写清「以下分析仅基于最近 {cal_days} 天样本」，"
            f"禁止写「基于近 {lookback} 天采集」。"
        )
        if supplemented:
            hint += " 已尝试按月搜索补更早样本，仍可能未穷尽该账号全部历史。"
    else:
        hint = (
            f"实际样本 {sample_count} 条，覆盖 {oldest} 至 {newest}（{cal_days} 天）。"
            f"{est_daily}"
        )

    return {
        "lookback_days_requested": lookback,
        "actual_calendar_days_covered": cal_days,
        "oldest_tweet_date": oldest,
        "newest_tweet_date": newest,
        "sample_count": sample_count,
        "full_window_covered": window_meta.get("full_window_covered", False),
        "coverage_incomplete": incomplete,
        "reached_lookback_cutoff": reached,
        "stopped_reason": tl_fetch.get("stopped_reason"),
        "model_writing_hint": hint,
    }


def _quarter_ranges(lookback_days: int) -> list[tuple]:
    """将近 lookback_days 切成 4 段，用于按季搜索补样（比 12 次月搜更省配额）。"""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=lookback_days)
    chunk = max(1, lookback_days // 4)
    ranges: list[tuple] = []
    cur = start
    while cur < today:
        end = min(cur + timedelta(days=chunk), today + timedelta(days=1))
        ranges.append((cur, end))
        cur = end
    return ranges


async def _fetch_quarterly_search_samples(
    client: Client,
    screen_name: str,
    lookback_days: int,
    per_quarter: int = 30,
) -> tuple[list, dict[str, Any]]:
    """按季度搜索补样，覆盖近一年各时段（适合超高频账号）。"""
    collected: list = []
    seen: set[str] = set()
    quarter_stats: dict[str, int] = {}
    errors: list[str] = []

    for start, end in _quarter_ranges(lookback_days):
        label = f"{start.isoformat()}~{end.isoformat()}"
        q = f"from:{screen_name} since:{start} until:{end}"
        month_batch: list = []
        try:
            await asyncio.sleep(_PAGE_DELAY_SEC)
            result = await client.search_tweet(q, "Latest", count=20)
            month_batch.extend(list(result))
            if result.next_cursor and per_quarter > 20:
                await asyncio.sleep(_PAGE_DELAY_SEC)
                try:
                    more = await result.next()
                    if more:
                        month_batch.extend(list(more))
                except TwitterException as exc:
                    errors.append(f"{label} page2: {exc}")
        except TwitterException as exc:
            errors.append(f"{label}: {exc}")
            continue

        added = 0
        for t in month_batch[:per_quarter]:
            tid = str(t.id)
            if tid not in seen:
                seen.add(tid)
                collected.append(t)
                added += 1
        quarter_stats[label] = added

    return collected, {
        "quarters_searched": len(_quarter_ranges(lookback_days)),
        "tweets_added": len(collected),
        "per_quarter_target": per_quarter,
        "quarter_hits": quarter_stats,
        "errors": errors,
    }


def _compact_tweet_rows(rows: list[dict[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    """压缩推文列表，避免 MCP 返回体过大导致工具失败。"""
    out: list[dict[str, Any]] = []
    for r in rows[:limit]:
        dt = _parse_twitter_dt(r.get("created_at", ""))
        out.append(
            {
                "id": r["id"],
                "date": _dt_to_date_str(dt),
                "post_type": r.get("post_type"),
                "likes": r.get("likes", 0),
                "retweets": r.get("retweets", 0),
                "text": (r.get("text") or "")[:280],
                "retweet_source": r.get("retweet_source"),
            }
        )
    return out


def _build_evidence_pack(
    rows: list[dict[str, Any]], limit: int = 35
) -> list[dict[str, Any]]:
    """预遴选立证推文：按议题分组取高互动帖 + 补足总量，保留较长原文供正文引用。"""
    seen: set[str] = set()
    pack: list[dict[str, Any]] = []

    def _row_to_evidence(r: dict[str, Any], theme: str) -> dict[str, Any]:
        dt = _parse_twitter_dt(r.get("created_at", ""))
        return {
            "id": r["id"],
            "date": _dt_to_date_str(dt),
            "theme": theme,
            "post_type": r.get("post_type"),
            "likes": r.get("likes", 0),
            "retweets": r.get("retweets", 0),
            "replies": r.get("replies", 0),
            "text": (r.get("text") or "")[:500],
            "retweet_source": r.get("retweet_source"),
        }

    by_theme: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_theme[_classify_viewpoint(r)].append(r)

    for theme, items in sorted(by_theme.items(), key=lambda x: len(x[1]), reverse=True):
        items.sort(
            key=lambda r: (
                r.get("likes", 0) + r.get("retweets", 0) * 2,
                len(r.get("text") or ""),
            ),
            reverse=True,
        )
        for r in items[:4]:
            tid = str(r.get("id"))
            if tid not in seen and len(pack) < limit:
                seen.add(tid)
                pack.append(_row_to_evidence(r, theme))

    all_sorted = sorted(
        rows,
        key=lambda r: r.get("likes", 0) + r.get("retweets", 0) * 2,
        reverse=True,
    )
    for r in all_sorted:
        if len(pack) >= limit:
            break
        tid = str(r.get("id"))
        if tid not in seen:
            seen.add(tid)
            pack.append(_row_to_evidence(r, _classify_viewpoint(r)))

    pack.sort(
        key=lambda x: x.get("date") or "",
        reverse=True,
    )
    return pack


async def _fetch_search_fallback(
    client: Client, screen_name: str, lookback_days: int, limit: int = 30
) -> tuple[list, list[str]]:
    """时间线不足时单次搜索补样。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
    q = f"from:{screen_name} since:{cutoff}"
    try:
        await asyncio.sleep(_PAGE_DELAY_SEC)
        result = await client.search_tweet(q, "Latest", count=20)
        return list(result)[:limit], []
    except TwitterException as exc:
        return [], [str(exc)]


def _merge_tweet_objects(primary: list, extra: list) -> list:
    seen = {str(t.id) for t in primary}
    merged = list(primary)
    for t in extra:
        tid = str(t.id)
        if tid not in seen:
            seen.add(tid)
            merged.append(t)
    merged.sort(
        key=lambda t: _parse_twitter_dt(str(t.created_at)) or datetime.min.replace(
            tzinfo=timezone.utc
        ),
        reverse=True,
    )
    return merged


def _build_author_voice(rows: list[dict[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    """博主本人发声：原创 + 回复 + 引用推（非纯转发），按互动量排序。"""
    voice_types = {"original", "reply", "quote"}
    voice = [r for r in rows if r.get("post_type") in voice_types]
    voice.sort(
        key=lambda r: (
            r.get("likes", 0) + r.get("retweets", 0) * 2 + r.get("replies", 0),
            len(r.get("text") or ""),
        ),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for r in voice[:limit]:
        out.append(
            {
                "id": r["id"],
                "post_type": r["post_type"],
                "created_at": r["created_at"],
                "text": r["text"],
                "likes": r["likes"],
                "retweets": r["retweets"],
                "replies": r["replies"],
                "hashtags": r.get("hashtags") or [],
                "quoted_author": r.get("quoted_author"),
                "quoted_text": r.get("quoted_text"),
                "is_link_only": r.get("is_link_only"),
            }
        )
    return out


def _build_viewpoint_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """从原创/回复/引用中统计标签、@、转发源，便于模型写观点。"""
    voice_types = {"original", "reply", "quote"}
    voice = [r for r in rows if r.get("post_type") in voice_types]
    rt_sources = [
        r["retweet_source"]
        for r in rows
        if r.get("post_type") == "retweet" and r.get("retweet_source")
    ]
    hashtags: Counter[str] = Counter()
    mentions: Counter[str] = Counter()
    for r in voice:
        for tag in r.get("hashtags") or []:
            hashtags[tag.lower()] += 1
        for m in _extract_mentions(r.get("text") or ""):
            mentions[m.lower()] += 1

    link_only_count = sum(1 for r in voice if r.get("is_link_only"))
    long_originals = [
        {
            "created_at": r["created_at"],
            "likes": r["likes"],
            "text_preview": (r.get("text") or "")[:280],
        }
        for r in voice
        if r.get("post_type") == "original"
        and not r.get("is_link_only")
        and len(r.get("text") or "") >= 80
    ]
    long_originals.sort(key=lambda x: x["likes"], reverse=True)

    return {
        "author_voice_count": len(voice),
        "retweet_count": sum(1 for r in rows if r.get("post_type") == "retweet"),
        "link_only_original_count": link_only_count,
        "top_hashtags_in_author_voice": dict(hashtags.most_common(25)),
        "top_mentions_in_author_voice": dict(mentions.most_common(25)),
        "top_retweet_sources": dict(Counter(rt_sources).most_common(30)),
        "notable_long_originals": long_originals[:20],
    }


def _patch_get_client() -> None:
    import twitter_mcp.server as srv

    proxy = _resolve_proxy()
    cookies_path = srv.COOKIES_PATH

    async def _get_client() -> Client:
        cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
        client = Client("en", proxy=proxy)
        client.set_cookies(
            {"auth_token": cookies["auth_token"], "ct0": cookies["ct0"]}
        )
        return client

    srv._get_client = _get_client  # type: ignore[method-assign]


def _register_persona_tools() -> None:
    import twitter_mcp.server as srv

    @srv.mcp.tool()
    async def get_user_tweets_for_persona(
        screen_name: str,
        lookback_days: int = 90,
        max_tweets: int = 200,
        include_replies_sample: int = 0,
        monthly_search_per_month: int = 0,
    ) -> str:
        """
        深度人物画像专用（Twitter 第一轮唯一主工具）。

        已含 user_profile（主页）+ tweet_evidence_pack + compliance_gate。
        禁止再调 get_user_info（已从 MCP 注销）；勿用 get_tweet_evidence_for_persona 代替本工具。

        【重要】本工具返回含 compliance_gate：禁止立即写报告。
        须分轮完成 pending_tools（OCR 若 required → maigret）后再成稿。
        成稿第一行必须是「# 结构化预分析」，见 mandatory_output_contract。
        media_for_ocr 为空时成稿以「六、人物深度报告画像」结束，禁止 OCR 节。
        """
        try:
            return await _persona_impl(
                srv,
                screen_name,
                lookback_days,
                max_tweets,
                include_replies_sample,
                monthly_search_per_month,
            )
        except Exception as exc:
            return srv._dumps(
                {
                    "screen_name": screen_name,
                    "error": f"画像工具异常: {exc}",
                    "sample_size": 0,
                    "hint": "可再调一次 get_tweet_evidence_for_persona",
                }
            )

    @srv.mcp.tool()
    async def get_tweet_evidence_for_persona(
        screen_name: str,
        lookback_days: int = 90,
    ) -> str:
        """
        轻量立证包：仅当 get_user_tweets_for_persona 已调用且上下文仍缺推文原文时使用。
        禁止作为画像第一轮工具；禁止与 get_user_tweets_for_persona 重复调用。
        """
        try:
            return await _evidence_only_impl(srv, screen_name, lookback_days)
        except Exception as exc:
            return srv._dumps(
                {
                    "screen_name": screen_name,
                    "error": f"立证工具异常: {exc}",
                    "hint": "检查 TWITTER_PROXY 或稍后重试",
                }
            )


async def _evidence_only_impl(
    srv: Any, screen_name: str, lookback_days: int
) -> str:
    """轻量采集：条数更少，返回体约 1～3 万字符。"""
    return await _persona_impl(
        srv,
        screen_name,
        lookback_days,
        max_tweets=120,
        include_replies_sample=0,
        monthly_search_per_month=0,
        evidence_only=True,
    )


async def _persona_impl(
    srv: Any,
    screen_name: str,
    lookback_days: int,
    max_tweets: int,
    include_replies_sample: int,
    monthly_search_per_month: int,
    evidence_only: bool = False,
) -> str:
    fetch_warnings: list[str] = []
    client = await srv._get_client()

    try:
        user = await client.get_user_by_screen_name(screen_name)
    except TwitterException as exc:
        return srv._dumps(
            {
                "screen_name": screen_name,
                "error": f"无法获取用户: {exc}",
                "sample_size": 0,
            }
        )

    lookback = min(max(lookback_days, 30), _persona_lookback_days())
    cap = min(max(max_tweets, 40), _persona_max_tweets())
    max_pages = min(max(cap // _PAGE_SIZE, 2), _MAX_TIMELINE_PAGES)
    replies_pages = (
        min(max(include_replies_sample // _PAGE_SIZE, 1), _MAX_REPLY_PAGES)
        if include_replies_sample > 0
        else 0
    )

    raw_timeline, tl_fetch = await _fetch_timeline_until_lookback(
        client, user.id, "Tweets", lookback, cap, max_pages
    )
    if tl_fetch.get("errors"):
        fetch_warnings.extend(tl_fetch["errors"])

    supplemented = False
    timeline, window_meta = _filter_by_lookback(
        raw_timeline, lookback, tl_fetch.get("reached_lookback_cutoff", False)
    )

    if window_meta.get("coverage_incomplete") and window_meta.get(
        "actual_calendar_days_covered", 0
    ) < lookback - 7:
        extra, search_meta = await _fetch_monthly_search_samples(
            client, screen_name, lookback, per_month=18
        )
        if search_meta.get("errors"):
            fetch_warnings.extend(search_meta["errors"])
        if extra:
            supplemented = True
            raw_timeline = _merge_tweet_objects(raw_timeline, extra)
            timeline, window_meta = _filter_by_lookback(
                raw_timeline, lookback, tl_fetch.get("reached_lookback_cutoff", False)
            )

    if len(raw_timeline) < 10:
        extra, s_errs = await _fetch_search_fallback(
            client, screen_name, lookback, limit=30
        )
        if s_errs:
            fetch_warnings.extend(s_errs)
        if extra:
            raw_timeline = _merge_tweet_objects(raw_timeline, extra)
            timeline, window_meta = _filter_by_lookback(
                raw_timeline, lookback, tl_fetch.get("reached_lookback_cutoff", False)
            )

    replies: list = []
    replies_meta: dict[str, Any] = {"skipped": True}
    if replies_pages > 0:
        replies_raw, replies_meta = await _fetch_timeline_pages(
            client, user.id, "Replies", replies_pages
        )
        if replies_meta.get("errors"):
            fetch_warnings.extend(replies_meta["errors"])
        replies, _ = _filter_by_lookback(replies_raw, lookback)
        replies_meta["skipped"] = False

    if not timeline and not replies:
        return srv._dumps(
            {
                "screen_name": screen_name,
                "error": "推文采集为空，可能限流，请稍后重试或调 get_tweet_evidence_for_persona",
                "fetch_warnings": fetch_warnings,
                "sample_size": 0,
            }
        )

    timeline_rows = [_tweet_to_persona_dict(t) for t in timeline]
    reply_rows = [_tweet_to_persona_dict(t) for t in replies]
    all_rows = timeline_rows + reply_rows

    coverage_summary = _build_coverage_summary(
        lookback,
        len(timeline_rows),
        window_meta,
        tl_fetch,
        supplemented,
        user.statuses_count,
    )

    payload = _assemble_slim_persona_payload(
        screen_name=screen_name,
        all_rows=all_rows,
        timeline_rows=timeline_rows,
        reply_rows=reply_rows,
        user=user,
        fetch_warnings=fetch_warnings,
        coverage_summary=coverage_summary,
        window_meta=window_meta,
        tl_fetch=tl_fetch,
        supplemented=supplemented,
        replies_meta=replies_meta,
        evidence_limit=40,
    )
    if evidence_only:
        payload = {
            "compliance_gate": payload["compliance_gate"],
            "section2_citation_template": payload.get("section2_citation_template"),
            "mandatory_output_contract": payload["mandatory_output_contract"],
            "analysis_target_lock": payload["analysis_target_lock"],
            "user_profile": payload.get("user_profile"),
            "report_writing_requirements": payload["report_writing_requirements"],
            "tweet_evidence_pack": payload["tweet_evidence_pack"],
            "sample_coverage": payload["sample_coverage"],
            "viewpoint_theme_summary": payload["viewpoint_theme_summary"],
            "screen_name": screen_name,
            "sample_size": payload["sample_size"],
            "content_mode_hint": payload["content_mode_hint"],
        }
    payload["_payload_meta"] = {
        "chars": len(srv._dumps(payload)),
        "evidence_count": len(payload.get("tweet_evidence_pack") or []),
        "under_hermes_100k": len(srv._dumps(payload)) < 100_000,
    }
    return _persona_tool_result(srv, payload)

    # 避免 Hermes 工具名冲突：仅注册新工具，不覆盖原 get_user_tweets


def _prune_tools_for_hermes_persona() -> None:
    """从 MCP 注销易误导画像流程的工具（config include 之外的二次保险）。"""
    import twitter_mcp.server as srv

    for name in (
        "get_user_info",
        "get_user_tweets",
    ):
        try:
            srv.mcp.remove_tool(name)
        except Exception:
            pass


def main() -> None:
    _patch_get_client()
    _register_persona_tools()
    _prune_tools_for_hermes_persona()
    from twitter_mcp.server import main as twikit_main

    twikit_main()


if __name__ == "__main__":
    main()
