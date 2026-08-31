"""从 collect_profiles / collect_posts 发现待入库图片。"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from image_pipeline import mysql_store

logger = logging.getLogger(__name__)

# 主页封面常见字段名（列 + raw_json）
_COVER_KEYS = (
    "cover_url",
    "cover",
    "banner_url",
    "banner",
    "profile_banner_url",
    "profile_banner",
    "header_image",
    "headerImage",
    "coverPhoto",
    "cover_photo",
    "background_image",
    "backgroundImage",
)

_URL_RE = re.compile(r"^https?://", re.I)
_IMAGE_EXT_RE = re.compile(r"\.(jpe?g|png|gif|webp|bmp)(\?|$)", re.I)


def make_image_id(
    task_id: str,
    source_type: str,
    platform: str,
    account_id: str,
    post_id: str,
    origin_url: str,
) -> str:
    """业务键稳定生成 image_id，保证重复跑幂等。"""
    raw = "|".join(
        [
            task_id or "",
            source_type or "",
            (platform or "").lower(),
            account_id or "",
            post_id or "",
            (origin_url or "").strip(),
        ]
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    return f"img_{digest}"


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}
    return {}


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, list) else []
        except Exception:
            return []
    return []


def _looks_like_image_url(url: str) -> bool:
    if not url or not _URL_RE.match(url):
        return False
    lower = url.lower()
    # 明显视频后缀跳过
    if any(ext in lower for ext in (".mp4", ".m3u8", ".webm", ".mov", ".avi")):
        return False
    return True


def _first_str(*vals: Any) -> Optional[str]:
    for v in vals:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return None


def _pick_cover_from_raw(raw: Dict[str, Any]) -> Optional[str]:
    for key in _COVER_KEYS:
        if key in raw:
            url = _first_str(raw.get(key))
            if url and _looks_like_image_url(url):
                return url
    # 嵌套 user / profile
    for nest_key in ("user", "profile", "author", "channel"):
        nested = raw.get(nest_key)
        if isinstance(nested, dict):
            url = _pick_cover_from_raw(nested)
            if url:
                return url
    return None


def _media_urls(media_json: Any, raw_json: Any) -> List[str]:
    urls: List[str] = []
    seen: Set[str] = set()

    def add(u: Optional[str]) -> None:
        if not u or not _looks_like_image_url(u):
            return
        if u in seen:
            return
        seen.add(u)
        urls.append(u)

    for item in _as_list(media_json):
        if isinstance(item, str):
            add(item)
            continue
        if not isinstance(item, dict):
            continue
        typ = str(item.get("type") or item.get("media_type") or "").lower()
        if typ in {"video", "gif", "animated_gif"} and not item.get("url"):
            # 仅 thumb 的视频封面仍可入库
            add(_first_str(item.get("thumb"), item.get("thumbnail"), item.get("preview")))
            continue
        if typ in {"video"}:
            add(_first_str(item.get("thumb"), item.get("thumbnail"), item.get("cover"), item.get("url")))
            continue
        add(
            _first_str(
                item.get("url"),
                item.get("display_url"),
                item.get("media_url"),
                item.get("media_url_https"),
                item.get("src"),
                item.get("thumb"),
            )
        )

    raw = _as_dict(raw_json)
    # Twitter MCP：顶层 media_urls 字符串列表
    for url in _as_list(raw.get("media_urls")):
        if isinstance(url, str):
            add(url)
        elif isinstance(url, dict):
            add(
                _first_str(
                    url.get("url"),
                    url.get("media_url_https"),
                    url.get("media_url"),
                    url.get("thumb"),
                )
            )
    # 常见媒体数组字段
    for key in ("media", "images", "photos", "attachments", "entities"):
        val = raw.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, dict):
                    add(
                        _first_str(
                            item.get("url"),
                            item.get("media_url_https"),
                            item.get("media_url"),
                            item.get("display_url"),
                            item.get("thumb"),
                        )
                    )
        elif isinstance(val, dict):
            media = val.get("media")
            if isinstance(media, list):
                for item in media:
                    if isinstance(item, dict):
                        add(_first_str(item.get("media_url_https"), item.get("media_url"), item.get("url")))

    return urls


def _row(
    task_id: str,
    task_type: Optional[str],
    source_type: str,
    platform: str,
    account_id: str,
    post_id: str,
    profile_id: str,
    origin_url: str,
) -> Dict[str, Any]:
    return {
        "image_id": make_image_id(task_id, source_type, platform, account_id, post_id, origin_url),
        "task_id": task_id,
        "task_type": task_type,
        "source_type": source_type,
        "platform": platform,
        "account_id": account_id or None,
        "post_id": post_id or None,
        "profile_id": profile_id or account_id or None,
        "origin_url": origin_url,
    }


def discover_images(task_id: str) -> List[Dict[str, Any]]:
    """扫描任务下主页与发文，返回去重后的图片候选列表。"""
    task = mysql_store.fetch_task(task_id)
    if not task:
        raise RuntimeError(f"任务不存在: {task_id}")
    task_type = task.get("task_type")

    results: List[Dict[str, Any]] = []
    seen_keys: Set[Tuple[str, str, str, str, str]] = set()

    def push(item: Dict[str, Any]) -> None:
        key = (
            item["source_type"],
            (item.get("platform") or "").lower(),
            item.get("account_id") or "",
            item.get("post_id") or "",
            item["origin_url"],
        )
        if key in seen_keys:
            return
        seen_keys.add(key)
        results.append(item)

    for prof in mysql_store.fetch_profiles(task_id):
        platform = str(prof.get("platform") or "")
        account_id = str(prof.get("account_id") or "")
        avatar = _first_str(prof.get("avatar_url"))
        if avatar and _looks_like_image_url(avatar):
            push(
                _row(
                    task_id,
                    task_type,
                    "profile_avatar",
                    platform,
                    account_id,
                    "",
                    account_id,
                    avatar,
                )
            )
        raw = _as_dict(prof.get("raw_json"))
        cover = _pick_cover_from_raw(raw)
        # extra_json 里也可能有封面
        if not cover:
            cover = _pick_cover_from_raw(_as_dict(prof.get("extra_json")))
        if cover:
            push(
                _row(
                    task_id,
                    task_type,
                    "profile_cover",
                    platform,
                    account_id,
                    "",
                    account_id,
                    cover,
                )
            )

    for post in mysql_store.fetch_posts(task_id):
        platform = str(post.get("platform") or "")
        account_id = str(post.get("account_id") or "")
        post_id = str(post.get("content_id") or "")
        for url in _media_urls(post.get("media_json"), post.get("raw_json")):
            push(
                _row(
                    task_id,
                    task_type,
                    "post_media",
                    platform,
                    account_id,
                    post_id,
                    account_id,
                    url,
                )
            )

    logger.info("discover task=%s images=%s", task_id, len(results))
    return results
