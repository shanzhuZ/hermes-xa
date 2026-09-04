"""Maigret collect_accounts → cross_platform_candidates。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from collect_01.normalizers.base import first_str, safe_int

# 步骤二节点文案：排查平台数展示下限（避免 top_sites=10 显得过少）
_MAIGRET_SITES_DISPLAY_FLOOR = 500
# all_sites 时展示口径（贴近 Maigret 全库体量）
_MAIGRET_ALL_SITES_DISPLAY = 3000


def resolve_sites_scanned(
    data: Optional[Dict[str, Any]] = None,
    tool_args: Optional[Dict[str, Any]] = None,
) -> int:
    """解析本次 Maigret 排查平台数；展示时不低于下限。"""
    n: Optional[int] = None
    payload = data if isinstance(data, dict) else {}
    args = tool_args if isinstance(tool_args, dict) else {}

    if args.get("all_sites") or str(payload.get("scan_mode") or "").strip() == "all_sites":
        n = _MAIGRET_ALL_SITES_DISPLAY
    if n is None:
        mode = str(payload.get("scan_mode") or "").strip()
        m = re.match(r"^top[_-]?(\d+)$", mode, re.I)
        if m:
            n = safe_int(m.group(1))
    if n is None:
        n = safe_int(payload.get("top_sites"))
    if n is None:
        n = safe_int(args.get("top_sites"))
    if n is None or n <= 0:
        n = _MAIGRET_SITES_DISPLAY_FLOOR
    return max(int(n), _MAIGRET_SITES_DISPLAY_FLOOR)


def format_step2_message(
    cand_count: int,
    *,
    sites_scanned: Optional[int] = None,
    result_data: Optional[Dict[str, Any]] = None,
    tool_args: Optional[Dict[str, Any]] = None,
) -> str:
    """步骤二 completed 文案：经过 N 个平台排查发现了 M 个候选。"""
    n = (
        int(sites_scanned)
        if sites_scanned is not None and int(sites_scanned) > 0
        else resolve_sites_scanned(result_data, tool_args)
    )
    n = max(n, _MAIGRET_SITES_DISPLAY_FLOOR)
    return f"经过{n}个平台排查发现了{int(cand_count)}个候选"


def _platform_slug(item: Dict[str, Any]) -> Optional[str]:
    raw = first_str(item.get("platform"), item.get("site"), item.get("sitename"))
    if not raw:
        return None
    token = raw.lower().strip()
    mapping = {
        "x": "twitter",
        "twitter": "twitter",
        "youtube": "youtube",
        "instagram": "instagram",
        "tiktok": "tiktok",
        "telegram": "telegram",
        "weibo": "weibo",
        "bilibili": "bilibili",
        "imginn": "imginn",
        "github": "github",
        "githubgist": "github",
        "gist": "github",
    }
    for key, val in mapping.items():
        if key in token:
            return val
    return re.sub(r"[^a-z0-9]+", "", token) or token


def _handle_from_url(url: str) -> Optional[str]:
    u = (url or "").strip().rstrip("/")
    if not u:
        return None
    parts = [p for p in u.split("/") if p and p not in {"www", "http:", "https:"}]
    if not parts:
        return None
    last = parts[-1]
    if last in {"about", "profile"} and len(parts) >= 2:
        last = parts[-2]
    return last.lstrip("@") or None


def _youtube_channel_id_from_item(item: Dict[str, Any]) -> Optional[str]:
    """Maigret YouTube 常把正式 UC 放在 ids.youtube_channel_id，url 仍是 @handle。"""
    from collect_01.normalizers.youtube import youtube_channel_id_ok

    ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
    cid = first_str(
        ids.get("youtube_channel_id"),
        ids.get("channel_id"),
        item.get("youtube_channel_id"),
        item.get("channel_id"),
        item.get("channelId"),
    )
    if cid and youtube_channel_id_ok(str(cid)):
        return str(cid).strip()
    return None


def normalize_candidates(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    summary = data.get("summary") or data
    accounts = summary.get("accounts") or data.get("accounts") or []
    rows: List[Dict[str, Any]] = []
    platforms: List[str] = []
    for item in accounts if isinstance(accounts, list) else []:
        if not isinstance(item, dict):
            continue
        platform = _platform_slug(item)
        account_id = first_str(item.get("account_id"), item.get("uid"), item.get("url"))
        if not platform or not account_id:
            continue
        handle = first_str(item.get("username"), item.get("handle")) or _handle_from_url(str(account_id))
        # YouTube：优先正式 UC…，避免 account_id 落成 youtube.com/@handle 导致步骤4无法调 MCP
        if platform == "youtube":
            yt_cid = _youtube_channel_id_from_item(item)
            if yt_cid:
                account_id = yt_cid
        # GitHub：gist/主页 URL 时优先 username，便于 Apify
        elif platform == "github" and handle:
            aid = str(account_id)
            if "github.com" in aid or "gist.github" in aid or aid.startswith("http"):
                account_id = handle
        platforms.append(platform)
        rows.append(
            {
                "task_id": ctx["task_id"],
                "platform": platform,
                "account_id": str(account_id)[:128],
                "account_handle": handle,
                "confidence": item.get("confidence"),
                "evidence_json": item.get("evidence") or {},
                "match_strategy": "maigret",
                "status": item.get("status", "candidate"),
                "raw_json": item,
                "tool_output_id": ctx.get("tool_output_id"),
            }
        )
    tool_args = ctx.get("tool_args") if isinstance(ctx.get("tool_args"), dict) else None
    sites_scanned = resolve_sites_scanned(data, tool_args)
    return {
        "profiles": [],
        "posts": [],
        "candidates": rows,
        "platforms": platforms,
        "sites_scanned": sites_scanned,
    }
