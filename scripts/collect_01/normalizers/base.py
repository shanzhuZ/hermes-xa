"""Normalizer 公共工具。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional


def parse_json(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def unwrap_tool_payload(raw: Any) -> Any:
    """解析 Hook 落库的 tool_output / MCP 返回（含 result 字符串与 structuredContent）。"""
    data = parse_json(raw)
    if not isinstance(data, dict):
        return data
    sc = data.get("structuredContent")
    if isinstance(sc, dict) and (sc.get("items") is not None or sc.get("summary") is not None):
        return sc
    inner = data.get("result")
    if inner is None:
        return data
    if isinstance(inner, (dict, list)):
        return inner
    if isinstance(inner, str):
        parsed = parse_json(inner)
        if parsed is not None:
            return parsed
    return data


def safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def safe_bool(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if str(value).lower() in {"1", "true", "yes"}:
        return 1
    if str(value).lower() in {"0", "false", "no"}:
        return 0
    return None


def first_str(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def profile_row(
    ctx: Dict[str, Any],
    *,
    platform: str,
    account_id: str,
    account_handle: Optional[str] = None,
    display_name: Optional[str] = None,
    bio: Optional[str] = None,
    avatar_url: Optional[str] = None,
    profile_url: Optional[str] = None,
    follower_count: Optional[int] = None,
    following_count: Optional[int] = None,
    content_count: Optional[int] = None,
    verified: Optional[int] = None,
    raw: Any = None,
    collect_status: str = "success",
) -> Dict[str, Any]:
    return {
        "task_id": ctx["task_id"],
        "platform": platform,
        "account_id": account_id,
        "account_handle": account_handle,
        "display_name": display_name,
        "bio": bio,
        "avatar_url": avatar_url,
        "profile_url": profile_url,
        "follower_count": follower_count,
        "following_count": following_count,
        "content_count": content_count,
        "verified": verified,
        "visibility": "public",
        "collect_status": collect_status,
        "tool_output_id": ctx.get("tool_output_id"),
        "raw_json": json.dumps(raw, ensure_ascii=False, default=str) if raw is not None else None,
    }


def post_row(
    ctx: Dict[str, Any],
    *,
    platform: str,
    account_id: str,
    content_id: str,
    content_type: str = "post",
    title: Optional[str] = None,
    content_text: Optional[str] = None,
    content_url: Optional[str] = None,
    published_at: Optional[str] = None,
    view_count: Optional[int] = None,
    like_count: Optional[int] = None,
    comment_count: Optional[int] = None,
    repost_count: Optional[int] = None,
    raw: Any = None,
) -> Dict[str, Any]:
    text = content_text or ""
    truncated = 1 if len(text) > 8000 else 0
    if truncated:
        text = text[:8000]
    return {
        "task_id": ctx["task_id"],
        "platform": platform,
        "account_id": account_id,
        "content_id": content_id,
        "content_type": content_type,
        "parent_content_id": None,
        "title": title,
        "content_text": text,
        "content_url": content_url,
        "published_at": published_at,
        "view_count": view_count,
        "like_count": like_count,
        "comment_count": comment_count,
        "repost_count": repost_count,
        "media_json": None,
        "tool_output_id": ctx.get("tool_output_id"),
        "raw_json": json.dumps(raw, ensure_ascii=False, default=str) if raw is not None else None,
        "text_truncated": truncated,
    }


def infer_mcp_server(tool_name: str) -> Optional[str]:
    if not tool_name:
        return None
    m = re.match(r"mcp_([^_]+)", tool_name)
    return m.group(1) if m else None
