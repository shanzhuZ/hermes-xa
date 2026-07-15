"""图片流与 OCR/Vision 工具参数的 URL 匹配。

模型常把 YouTube 头像从 yt3.ggpht.com/=s88 改写成
yt3.googleusercontent.com/=s900，精确匹配会失败；旧逻辑在 HTTP
分支末尾直接 return None，连「仅剩 1 条 pending」兜底都走不到。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from collect_01 import db

_YT_HOSTS = {
    "yt3.ggpht.com",
    "yt3.googleusercontent.com",
    "i.ytimg.com",
    "i1.ytimg.com",
    "i2.ytimg.com",
    "i3.ytimg.com",
    "i4.ytimg.com",
}

_TWITTER_SIZE_SUFFIX = re.compile(
    r"_(?:normal|bigger|mini|200x200|400x400)(?=\.(?:jpg|jpeg|png|webp)$)",
    re.I,
)


def extract_image_source(tool_args: Dict[str, Any]) -> str:
    for key in ("image_url", "input_data", "image_path", "file_path", "url"):
        val = tool_args.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def avatar_url_fingerprint(url: str) -> str:
    """跨 CDN 主机名 / 尺寸参数的稳态指纹。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw.lower()
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host in _YT_HOSTS or host.endswith(".ggpht.com") or host.endswith(".googleusercontent.com"):
        if "yt" in host or "ggpht" in host or "googleusercontent" in host:
            host = "yt3"
    path = unquote(parsed.path or "")
    # YouTube: /BeD1yiJb...=s88-c-k-... → 只保留 hash 段
    if "=" in path:
        path = path.split("=", 1)[0]
    path = _TWITTER_SIZE_SUFFIX.sub("", path)
    # GitHub: 忽略 ?v=4 等
    return f"{host}{path}".lower().rstrip("/")


def _platform_hints_from_args(tool_args: Dict[str, Any], source: str) -> List[str]:
    blob = " ".join(
        str(tool_args.get(k) or "")
        for k in ("question", "prompt", "image_url", "input_data", "url")
    ).lower()
    blob = f"{blob} {source.lower()}"
    hints: List[str] = []
    if any(k in blob for k in ("twitter", "推特", "x种子", "pbs.twimg", "x.com")):
        hints.append("twitter")
    if any(k in blob for k in ("youtube", "yt3.", "ggpht", "youtu.be", "油管")):
        hints.append("youtube")
    if any(k in blob for k in ("instagram", "cdninstagram", "ig_", "ins")):
        hints.append("instagram")
    if any(k in blob for k in ("github", "githubusercontent")):
        hints.append("github")
    if any(k in blob for k in ("tiktok",)):
        hints.append("tiktok")
    if any(k in blob for k in ("weibo", "微博")):
        hints.append("weibo")
    if any(k in blob for k in ("bilibili", "bili", "哔哩")):
        hints.append("bilibili")
    if any(k in blob for k in ("telegram", "t.me")):
        hints.append("telegram")
    if any(k in blob for k in ("facebook", "fbcdn")):
        hints.append("facebook")
    # 去重保序
    seen = set()
    out: List[str] = []
    for h in hints:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _pick_pending(
    streams: List[Dict[str, Any]],
    *,
    platform: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    pending = [
        row
        for row in streams
        if row.get("validation_status") == "pending"
        and (platform is None or row.get("source_platform") == platform)
    ]
    waiting = [row for row in pending if "OCR" in str(row.get("validation_detail") or "")]
    if len(waiting) == 1:
        return waiting[0]
    if len(pending) == 1:
        return pending[0]
    return None


def find_image_stream_for_tool(task_id: str, tool_args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    source = extract_image_source(tool_args)
    if not source:
        return None
    streams = db.fetch_all(
        """
        SELECT stream_id, source_platform, source_account_id, payload_url, validation_status, validation_detail
        FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image'
        """,
        (task_id,),
    )
    if not streams:
        return None

    if source.startswith(("http://", "https://")):
        for row in streams:
            if (row.get("payload_url") or "").strip() == source:
                return row
        base = source.split("?")[0]
        for row in streams:
            url = (row.get("payload_url") or "").split("?")[0]
            if url == base:
                return row
        src_fp = avatar_url_fingerprint(source)
        if src_fp:
            for row in streams:
                if avatar_url_fingerprint(str(row.get("payload_url") or "")) == src_fp:
                    return row
        # 仅允许同平台 pending 兜底；禁止跨平台（如 Instagram CDN → YouTube pending）
        for platform in _platform_hints_from_args(tool_args, source):
            hit = _pick_pending(streams, platform=platform)
            if hit:
                return hit
        return None

    basename = os.path.basename(source).lower()
    hints: List[str] = []
    if any(k in basename for k in ("twitter", "tw_", "x.com")):
        hints.append("twitter")
    if any(k in basename for k in ("youtube", "yt_", "yt3")):
        hints.append("youtube")
    if "instagram" in basename or "ig_" in basename:
        hints.append("instagram")
    if "github" in basename:
        hints.append("github")
    if "tiktok" in basename:
        hints.append("tiktok")
    if "weibo" in basename:
        hints.append("weibo")
    if "bilibili" in basename or "bili" in basename:
        hints.append("bilibili")
    for platform in hints:
        hit = _pick_pending(streams, platform=platform)
        if hit:
            return hit
    # 本地路径无平台线索时不跨平台乱配
    return None
