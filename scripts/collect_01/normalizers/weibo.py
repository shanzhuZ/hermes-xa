"""微博 MCP → collect_profiles / collect_posts。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from collect_01.normalizers.base import (
    first_str,
    parse_fuzzy_count,
    post_row,
    profile_row,
    published_at_from_item,
    safe_int,
)


def normalize_profile(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    user = _as_user_dict(raw)
    if not user:
        return {"profiles": [], "posts": [], "candidates": [], "platforms": []}

    account_id = first_str(user.get("id"), user.get("uid"), ctx.get("account_id"))
    if not account_id:
        return {"profiles": [], "posts": [], "candidates": [], "platforms": []}

    handle = first_str(user.get("screen_name"), user.get("name"))
    profile_url = first_str(user.get("profile_url"))
    if not profile_url and handle:
        profile_url = f"https://weibo.com/u/{account_id}"

    row = profile_row(
        ctx,
        platform="weibo",
        account_id=str(account_id),
        account_handle=handle,
        display_name=handle,
        bio=first_str(user.get("description"), user.get("bio")),
        avatar_url=first_str(user.get("avatar_hd"), user.get("profile_image_url"), user.get("avatar_large")),
        profile_url=profile_url,
        follower_count=parse_fuzzy_count(user.get("followers_count")),
        following_count=safe_int(first_str(user.get("follow_count"), user.get("friends_count"))),
        content_count=safe_int(user.get("statuses_count")),
        verified=1 if user.get("verified") else 0,
        raw=user,
    )
    return {"profiles": [row], "posts": [], "candidates": [], "platforms": ["weibo"]}


def normalize_posts(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    items = _extract_feed_items(raw)
    default_account_id = first_str(ctx.get("account_id")) or "unknown"
    rows: List[Dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        content_id = first_str(item.get("id"), item.get("mid"), item.get("bid"))
        if not content_id:
            continue

        user = item.get("user") if isinstance(item.get("user"), dict) else {}
        account_id = first_str(user.get("id"), user.get("uid"), default_account_id) or default_account_id
        text = first_str(item.get("raw_text"), item.get("text"))
        content_type = "repost" if item.get("retweeted_status") else "post"
        parent_id = None
        retweeted = item.get("retweeted_status")
        if isinstance(retweeted, dict):
            parent_id = first_str(retweeted.get("id"), retweeted.get("mid"))

        rows.append(
            post_row(
                ctx,
                platform="weibo",
                account_id=str(account_id),
                content_id=str(content_id),
                content_type=content_type,
                content_text=text,
                content_url=f"https://m.weibo.cn/detail/{content_id}",
                published_at=published_at_from_item(item, "created_at", "created_at_str"),
                like_count=safe_int(item.get("attitudes_count")),
                comment_count=safe_int(item.get("comments_count")),
                repost_count=safe_int(item.get("reposts_count")),
                raw=item,
            )
        )
        if parent_id:
            rows[-1]["parent_content_id"] = str(parent_id)

    return {"profiles": [], "posts": rows, "candidates": [], "platforms": ["weibo"] if rows else []}


def _as_user_dict(raw: Any) -> Optional[Dict[str, Any]]:
    if isinstance(raw, dict):
        if raw.get("screen_name") or raw.get("id") or raw.get("uid"):
            return raw
        for key in ("user", "userInfo", "data"):
            nested = raw.get(key)
            if isinstance(nested, dict) and (nested.get("screen_name") or nested.get("id")):
                return nested
    return None


def _extract_feed_items(raw: Any) -> List[Any]:
    if isinstance(raw, list):
        return raw

    data = raw if isinstance(raw, dict) else {}
    for key in ("items", "feeds", "data", "result", "statuses", "list"):
        val = data.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            nested = _extract_feed_items(val)
            if nested:
                return nested

    # MCP 偶发 {"result": {"result": [...]}}
    outer = data.get("result")
    if isinstance(outer, list):
        return outer
    if isinstance(outer, dict):
        inner = outer.get("result")
        if isinstance(inner, list):
            return inner
        nested = _extract_feed_items(outer)
        if nested:
            return nested

    return []
