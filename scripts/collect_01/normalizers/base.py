"""Normalizer 公共工具。"""

from __future__ import annotations

import json
import re
from datetime import datetime
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


def parse_fuzzy_count(value: Any) -> Optional[int]:
    """解析 3M / 1.2K / 3,078,658 等粉丝数字符串。"""
    if value is None:
        return None
    direct = safe_int(value)
    if direct is not None:
        return direct
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    m = re.match(r"^([\d.]+)\s*([KkMmBb])?$", text)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    suffix = (m.group(2) or "").upper()
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(suffix, 1)
    return int(num * mult)


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


_UNICODE_ESC_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def decode_escaped_text(value: Optional[str]) -> Optional[str]:
    """解码字面量 \\uXXXX / \\n（Apify Facebook 等把 Unicode 当明文返回）。

    仅当文本含 \\uXXXX 时处理，避免误伤普通反斜杠路径。
    """
    if value is None:
        return None
    s = str(value)
    if not _UNICODE_ESC_RE.search(s):
        return s
    out = _UNICODE_ESC_RE.sub(lambda m: chr(int(m.group(1), 16)), s)
    # 常见转义一并还原（Facebook intro 常带 \\n）
    out = (
        out.replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\\\\", "\\")
    )
    return out


def first_str(*values: Any) -> Optional[str]:
    for v in values:
        if v is None:
            continue
        s = decode_escaped_text(str(v).strip())
        if s:
            return s
    return None


def normalize_published_at(value: Any) -> Optional[str]:
    """统一成 MySQL DATETIME 可接受的 'YYYY-MM-DD HH:MM:SS'。

    支持：
    - 2026-06-28T01:00:29Z / 带毫秒 / 带时区偏移
    - Wed Jul 22 05:19:47 +0000 2026（Twitter）
    - Unix 秒/毫秒时间戳（int 或数字字符串）
    """
    if value is None or value == "":
        return None
    # Unix 时间戳（秒 / 毫秒）
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1e12:  # 毫秒
            ts = ts / 1000.0
        if 1e9 <= ts < 1e11:
            try:
                return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
    text = first_str(value)
    if not text:
        return None
    if re.match(r"^\d{10,13}$", text):
        try:
            ts = float(text)
            if ts > 1e12:
                ts = ts / 1000.0
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    # 已是 MySQL 友好格式
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", text):
        return text
    # ISO8601
    iso = text.replace("T", " ").strip()
    if iso.endswith("Z"):
        iso = iso[:-1]
    # 去掉 +08:00 / +0000 等偏移
    iso = re.sub(r"[+-]\d{2}:?\d{2}$", "", iso).strip()
    if "." in iso:
        iso = iso.split(".", 1)[0]
    if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", iso):
        return iso[:19]
    # Twitter / ctime 风格
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%a %b %d %H:%M:%S %Y"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def published_at_from_item(item: Dict[str, Any], *extra_keys: str) -> Optional[str]:
    """从发文原始字段中提取发布时间（多平台字段名兜底）。"""
    if not isinstance(item, dict):
        return None
    keys = (
        "published_at",
        "publishedAt",
        "created_at",
        "createdAt",
        "date",
        "datetime",
        "time",
        "timestamp",
        "takenAt",
        "taken_at",
        "createTime",
        "create_time",
        "created_time",
        "publishTime",
        "pushedAt",
        "updatedAt",
    ) + tuple(extra_keys)
    for key in keys:
        if key not in item:
            continue
        parsed = normalize_published_at(item.get(key))
        if parsed:
            return parsed
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
        "published_at": normalize_published_at(published_at),
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
    name = normalize_mcp_tool_name(tool_name)
    m = re.match(r"mcp_([^_]+)", name)
    return m.group(1) if m else None


def normalize_mcp_tool_name(tool_name: str) -> str:
    """统一 Hermes MCP 工具名。

    新版 gateway 常见：``mcp__twitter__get_user_info``
    本仓库 registry/phases 使用：``mcp_twitter_get_user_info``

    Apify Actor 保留工具段中的双下划线，例如：
    ``mcp__apify__apify__instagram_scraper`` → ``mcp_apify_apify__instagram_scraper``
    """
    name = (tool_name or "").strip()
    if not name.startswith("mcp__"):
        return name
    rest = name[5:]  # 去掉前缀 mcp__
    if "__" not in rest:
        return f"mcp_{rest}"
    server, tool = rest.split("__", 1)
    return f"mcp_{server}_{tool}"

