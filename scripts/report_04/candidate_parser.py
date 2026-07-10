"""写报步骤三：从 assistant 文本解析跨平台候选账号。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_PLATFORM_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("twitter", re.compile(r"(?:twitter|x)\.com/([A-Za-z0-9_]{1,50})", re.I)),
    ("twitter", re.compile(r"(?:推特|Twitter|X)\s*[@：:]\s*@?([A-Za-z0-9_]{1,50})", re.I)),
    ("youtube", re.compile(r"youtube\.com/(?:@|channel/|c/)([A-Za-z0-9_\-]{1,100})", re.I)),
    ("instagram", re.compile(r"instagram\.com/([A-Za-z0-9_.]{1,50})", re.I)),
    ("facebook", re.compile(r"facebook\.com/([A-Za-z0-9.]{1,50})", re.I)),
    ("telegram", re.compile(r"t\.me/([A-Za-z0-9_]{1,50})", re.I)),
    ("github", re.compile(r"github\.com/([A-Za-z0-9_\-]{1,50})", re.I)),
    ("weibo", re.compile(r"weibo\.com/(?:u/)?([0-9]+|[A-Za-z0-9_]{1,50})", re.I)),
    ("reddit", re.compile(r"reddit\.com/(?:u|user)/([A-Za-z0-9_\-]{1,50})", re.I)),
    ("linkedin", re.compile(r"linkedin\.com/in/([A-Za-z0-9_\-]{1,50})", re.I)),
    ("vk", re.compile(r"vk\.com/([A-Za-z0-9_.]{1,50})", re.I)),
    ("tiktok", re.compile(r"tiktok\.com/@([A-Za-z0-9_.]{1,50})", re.I)),
]

_HANDLE_LINE = re.compile(
    r"(?:(twitter|youtube|instagram|facebook|telegram|github|weibo|reddit|linkedin|vk|tiktok|推特|微博))"
    r"[^@\n]{0,20}[@：:]\s*@?([A-Za-z0-9_\.]{2,50})",
    re.I,
)


def _norm_platform(token: str) -> str:
    t = (token or "").lower()
    if t in {"推特", "x"}:
        return "twitter"
    if t == "微博":
        return "weibo"
    return t


def parse_web_search_candidates(text: str) -> List[Dict[str, Any]]:
    """从步骤三 assistant 输出解析候选账号。"""
    if not text or len(text.strip()) < 20:
        return []
    found: Dict[str, Dict[str, Any]] = {}
    for platform, pat in _PLATFORM_PATTERNS:
        for m in pat.finditer(text):
            handle = (m.group(1) or "").strip().rstrip("/")
            if not handle or handle.lower() in {"home", "watch", "results"}:
                continue
            key = f"{platform}:{handle.lower()}"
            if key not in found:
                found[key] = {
                    "platform": platform,
                    "account_handle": handle,
                    "account_id": handle,
                    "discovery_source": "web_search",
                    "profile_url": m.group(0),
                    "confidence": "medium",
                }
    for m in _HANDLE_LINE.finditer(text):
        platform = _norm_platform(m.group(1))
        handle = (m.group(2) or "").strip()
        if not platform or not handle:
            continue
        key = f"{platform}:{handle.lower()}"
        if key not in found:
            found[key] = {
                "platform": platform,
                "account_handle": handle,
                "account_id": handle,
                "discovery_source": "web_search",
                "confidence": "low",
            }
    return list(found.values())


def looks_like_step3_summary(text: str) -> bool:
    """是否像步骤三候选汇总（非纯进度句）。"""
    t = (text or "").replace(" ", "")
    if len(t) < 80:
        return False
    if re.search(r"步骤\s*3|步骤三|网页检索|web_search|候选", text, re.I):
        return True
    return len(parse_web_search_candidates(text)) >= 1
