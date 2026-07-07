"""Maigret collect_accounts → cross_platform_candidates。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from collect_01.normalizers.base import first_str, safe_int


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
    }
    for key, val in mapping.items():
        if key in token:
            return val
    return re.sub(r"[^a-z0-9]+", "", token) or token


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
        platforms.append(platform)
        rows.append(
            {
                "task_id": ctx["task_id"],
                "platform": platform,
                "account_id": str(account_id)[:128],
                "account_handle": first_str(item.get("username"), item.get("handle")),
                "confidence": item.get("confidence"),
                "evidence_json": item.get("evidence") or {},
                "match_strategy": "maigret",
                "status": item.get("status", "candidate"),
                "raw_json": item,
                "tool_output_id": ctx.get("tool_output_id"),
            }
        )
    return {"profiles": [], "posts": [], "candidates": rows, "platforms": platforms}
