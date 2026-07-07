"""Apify Actor / dataset → profile 或 posts（按上下文推断）。"""

from __future__ import annotations

from typing import Any, Dict, List

from collect_01.normalizers.base import first_str, post_row, profile_row, safe_int

_PLATFORM_FROM_CTX = {
    "instagram": "instagram",
    "tiktok": "tiktok",
    "telegram": "telegram",
}


def normalize_actor_run(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    # Actor 启动只记日志，等 get_dataset_items 落库
    return {"profiles": [], "posts": [], "candidates": [], "platforms": []}


def normalize_dataset_items(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    items = data.get("items") or data.get("data") or []
    if not isinstance(items, list):
        items = []
    platform = _guess_platform(ctx, items)
    if not platform:
        return {"profiles": [], "posts": [], "candidates": [], "platforms": []}

    profiles: List[Dict[str, Any]] = []
    posts: List[Dict[str, Any]] = []

    if platform == "instagram":
        profiles, posts = _instagram_items(ctx, items)
    elif platform == "tiktok":
        profiles, posts = _tiktok_items(ctx, items)
    elif platform == "telegram":
        profiles, posts = _telegram_items(ctx, items)

    plats = [platform] if profiles or posts else []
    return {"profiles": profiles, "posts": posts, "candidates": [], "platforms": plats}


def _guess_platform(ctx: Dict[str, Any], items: List[Any]) -> str:
    hint = str(ctx.get("platform_hint") or "").lower()
    for key in _PLATFORM_FROM_CTX:
        if key in hint:
            return key
    if items and isinstance(items[0], dict):
        sample = items[0]
        if "ownerUsername" in sample or "instagram" in str(sample.get("url", "")).lower():
            return "instagram"
        if "authorMeta" in sample or "tiktok" in str(sample.get("webVideoUrl", "")).lower():
            return "tiktok"
        if "channelUsername" in sample or sample.get("message"):
            return "telegram"
    return ""


def _instagram_items(ctx: Dict[str, Any], items: List[Any]):
    profiles: List[Dict[str, Any]] = []
    posts: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        username = first_str(item.get("ownerUsername"), item.get("username"))
        if username and not profiles:
            profiles.append(
                profile_row(
                    ctx,
                    platform="instagram",
                    account_id=username,
                    account_handle=username,
                    display_name=first_str(item.get("ownerFullName"), item.get("fullName")),
                    bio=first_str(item.get("biography")),
                    follower_count=safe_int(item.get("followersCount")),
                    content_count=safe_int(item.get("postsCount")),
                    profile_url=f"https://www.instagram.com/{username}/",
                    raw=item,
                    collect_status="success" if item else "empty",
                )
            )
        pid = first_str(item.get("id"), item.get("shortCode"))
        if pid and item.get("caption") is not None:
            posts.append(
                post_row(
                    ctx,
                    platform="instagram",
                    account_id=username or "unknown",
                    content_id=str(pid),
                    content_text=first_str(item.get("caption")),
                    like_count=safe_int(item.get("likesCount")),
                    comment_count=safe_int(item.get("commentsCount")),
                    raw=item,
                )
            )
    return profiles, posts


def _tiktok_items(ctx: Dict[str, Any], items: List[Any]):
    profiles: List[Dict[str, Any]] = []
    posts: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        author = item.get("authorMeta") or {}
        username = first_str(author.get("name"), item.get("author"))
        if username and not profiles:
            profiles.append(
                profile_row(
                    ctx,
                    platform="tiktok",
                    account_id=username,
                    account_handle=username,
                    display_name=first_str(author.get("nickName")),
                    follower_count=safe_int(author.get("fans")),
                    content_count=safe_int(author.get("video")),
                    profile_url=f"https://www.tiktok.com/@{username}",
                    raw=item,
                )
            )
        pid = first_str(item.get("id"), item.get("videoId"))
        if pid:
            posts.append(
                post_row(
                    ctx,
                    platform="tiktok",
                    account_id=username or "unknown",
                    content_id=str(pid),
                    content_type="video",
                    content_text=first_str(item.get("text"), item.get("desc")),
                    view_count=safe_int(item.get("playCount")),
                    like_count=safe_int(item.get("diggCount")),
                    comment_count=safe_int(item.get("commentCount")),
                    repost_count=safe_int(item.get("shareCount")),
                    raw=item,
                )
            )
    return profiles, posts


def _telegram_items(ctx: Dict[str, Any], items: List[Any]):
    profiles: List[Dict[str, Any]] = []
    posts: List[Dict[str, Any]] = []
    channel = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        channel = first_str(item.get("channelUsername"), item.get("channelName"), channel)
        if channel and not profiles:
            profiles.append(
                profile_row(
                    ctx,
                    platform="telegram",
                    account_id=channel,
                    account_handle=channel,
                    display_name=first_str(item.get("channelName")),
                    bio=first_str(item.get("channelDescription")),
                    profile_url=f"https://t.me/{channel}",
                    raw=item,
                )
            )
        pid = first_str(item.get("id"), item.get("messageId"))
        if pid:
            posts.append(
                post_row(
                    ctx,
                    platform="telegram",
                    account_id=channel or "unknown",
                    content_id=str(pid),
                    content_text=first_str(item.get("text"), item.get("message")),
                    view_count=safe_int(item.get("views")),
                    raw=item,
                )
            )
    return profiles, posts
