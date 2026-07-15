"""B站 MCP → collect_profiles。"""

from __future__ import annotations

from typing import Any, Dict

from collect_01.normalizers.base import first_str, profile_row, safe_int


def normalize_profile(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    if not data and isinstance(raw, str):
        return {"profiles": [], "posts": [], "candidates": [], "platforms": []}

    uid = first_str(data.get("uid"), ctx.get("account_id"))
    if not uid:
        return {"profiles": [], "posts": [], "candidates": [], "platforms": []}

    name = first_str(data.get("name"))
    space_url = first_str(data.get("space_url")) or f"https://space.bilibili.com/{uid}"

    row = profile_row(
        ctx,
        platform="bilibili",
        account_id=str(uid),
        account_handle=str(uid),
        display_name=name or str(uid),
        bio=first_str(data.get("bio")),
        avatar_url=first_str(data.get("avatar"), data.get("face")),
        profile_url=space_url,
        follower_count=safe_int(data.get("follower_count")),
        following_count=safe_int(data.get("following_count")),
        content_count=safe_int(data.get("video_count")),
        verified=0,
        raw=data,
    )
    return {"profiles": [row], "posts": [], "candidates": [], "platforms": ["bilibili"]}
