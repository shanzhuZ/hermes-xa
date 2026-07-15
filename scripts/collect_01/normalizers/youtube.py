"""YouTube MCP → collect_profiles / collect_posts。"""

from __future__ import annotations

from typing import Any, Dict, List

from collect_01.normalizers.base import first_str, post_row, profile_row, safe_int


def youtube_channel_id_ok(channel_id: str) -> bool:
    """正式 channelId：UC 开头且长度足够（通常 24；拒伪 UC+短 handle）。"""
    cid = (channel_id or "").strip()
    if not cid.upper().startswith("UC"):
        return False
    return len(cid) >= 22


def normalize_profile(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    ch = data.get("channel") or data.get("data") or data
    channel_id = first_str(ch.get("channelId"), ch.get("id"), ctx.get("account_id"))
    if not channel_id:
        return {"profiles": [], "posts": [], "candidates": [], "platforms": []}
    row = profile_row(
        ctx,
        platform="youtube",
        account_id=str(channel_id),
        account_handle=first_str(ch.get("customUrl"), ch.get("handle")),
        display_name=first_str(ch.get("title"), ch.get("channelName")),
        bio=first_str(ch.get("description")),
        avatar_url=first_str(
            (ch.get("thumbnails") or {}).get("default", {}).get("url")
            if isinstance(ch.get("thumbnails"), dict)
            else ch.get("thumbnailUrl")
        ),
        profile_url=f"https://www.youtube.com/channel/{channel_id}",
        follower_count=safe_int(ch.get("subscriberCount")),
        content_count=safe_int(ch.get("videoCount")),
        raw=data,
    )
    return {"profiles": [row], "posts": [], "candidates": [], "platforms": ["youtube"]}


def normalize_posts(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    videos = data.get("videos") or data.get("items") or data.get("analysis") or []
    if isinstance(videos, dict):
        videos = videos.get("videos") or videos.get("items") or []
    channel_id = first_str(ctx.get("account_id"), data.get("channelId")) or "unknown"
    rows: List[Dict[str, Any]] = []
    for item in videos if isinstance(videos, list) else []:
        if not isinstance(item, dict):
            continue
        vid = first_str(item.get("videoId"), item.get("id"))
        if not vid:
            continue
        rows.append(
            post_row(
                ctx,
                platform="youtube",
                account_id=str(channel_id),
                content_id=str(vid),
                content_type="video",
                title=first_str(item.get("title")),
                content_text=first_str(item.get("description")),
                content_url=f"https://www.youtube.com/watch?v={vid}",
                view_count=safe_int(item.get("viewCount")),
                like_count=safe_int(item.get("likeCount")),
                comment_count=safe_int(item.get("commentCount")),
                raw=item,
            )
        )
    return {"profiles": [], "posts": rows, "candidates": [], "platforms": ["youtube"] if rows else []}
