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


def handle_matches_seed(handle: str, seed_handle: str) -> bool:
    """web_search 候选 handle 是否与种子账号相关。"""
    h = (handle or "").lower().strip().lstrip("@")
    s = (seed_handle or "").lower().strip().lstrip("@")
    if not h or not s or len(h) < 3:
        return False
    if h in {"p", "s", "login", "search", "null", "reel", "articles", "contact", "home", "watch"}:
        return False
    if h == s:
        return True
    if len(h) >= 4 and len(s) >= 4 and (h.startswith(s) or s.startswith(h)):
        return True
    return False


def candidate_relevant_for_seed(row: Dict[str, Any], seed_handle: str) -> bool:
    """候选是否应进入步骤四采集列表。"""
    if str(row.get("match_strategy") or "") == "maigret":
        return True
    return handle_matches_seed(str(row.get("account_handle") or ""), seed_handle)


def relevant_profile_platforms(
    candidates: List[Dict[str, Any]],
    seed_handle: str,
) -> List[str]:
    """按平台去重，仅保留与种子相关的可采集平台。"""
    from report_04.phases import is_collectible_platform, profile_step_order

    found: set[str] = set()
    for row in candidates:
        plat = str(row.get("platform") or "").strip().lower()
        if not plat or not is_collectible_platform(plat):
            continue
        if candidate_relevant_for_seed(row, seed_handle):
            found.add(plat)
    return sorted(found, key=profile_step_order)


def parse_web_search_candidates_from_tool(tool_name: str, raw: Any) -> List[Dict[str, Any]]:
    """从 web_search / web_extract / browser 工具返回 JSON 中解析候选 URL。"""
    import json as _json

    if raw is None:
        return []
    if isinstance(raw, dict):
        blob = _json.dumps(raw, ensure_ascii=False)
    else:
        blob = str(raw)
    if len(blob) < 20:
        return []
    return parse_web_search_candidates(blob)
