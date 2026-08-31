"""Twitter MCP → collect_profiles / collect_posts。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from collect_01.normalizers.base import first_str, post_row, profile_row, published_at_from_item, safe_int


def _extract_post_media(item: Dict[str, Any]) -> Optional[List[Dict[str, str]]]:
    """从 MCP 推文字段抽出发文配图，供 image_pipeline discover 使用。"""
    out: List[Dict[str, str]] = []
    seen: set[str] = set()

    def add(typ: str, url: Optional[str], *, thumb: Optional[str] = None) -> None:
        u = (url or thumb or "").strip()
        if not u or not u.startswith("http") or u in seen:
            return
        seen.add(u)
        row: Dict[str, str] = {"type": typ or "photo", "url": u}
        if thumb and thumb.startswith("http"):
            row["thumb"] = thumb
        out.append(row)

    media_list = item.get("media")
    if isinstance(media_list, list):
        for m in media_list:
            if isinstance(m, str):
                add("photo", m)
                continue
            if not isinstance(m, dict):
                continue
            typ = str(m.get("type") or m.get("media_type") or "photo")
            url = first_str(m.get("url"), m.get("media_url_https"), m.get("media_url"))
            thumb = first_str(m.get("thumb"), m.get("thumbnail"), m.get("preview"))
            if typ.lower() in {"video", "animated_gif", "gif"}:
                add(typ, url or thumb, thumb=thumb or url)
            else:
                add(typ, url, thumb=thumb)

    for url in item.get("media_urls") or []:
        if isinstance(url, str):
            add("photo", url)
        elif isinstance(url, dict):
            add(
                str(url.get("type") or "photo"),
                first_str(url.get("url"), url.get("media_url_https")),
                thumb=first_str(url.get("thumb")),
            )

    return out or None


def normalize_profile(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    user = data.get("user") or data.get("data") or data
    if isinstance(user, dict) and "legacy" in user:
        user = {**user.get("legacy", {}), **user}
    account_id = first_str(user.get("rest_id"), user.get("id"), user.get("id_str"), ctx.get("account_id"))
    if not account_id:
        return {"profiles": [], "posts": [], "candidates": [], "platforms": []}
    handle = first_str(user.get("screen_name"), user.get("username"))
    row = profile_row(
        ctx,
        platform="twitter",
        account_id=str(account_id),
        account_handle=handle,
        display_name=first_str(user.get("name")),
        bio=first_str(user.get("description"), user.get("bio")),
        avatar_url=first_str(
            (user.get("profile_image_url_https") or user.get("profile_image_url") or "")
            .replace("_normal", "")
        ),
        profile_url=f"https://twitter.com/{handle}" if handle else None,
        follower_count=safe_int(user.get("followers_count")),
        following_count=safe_int(user.get("friends_count")),
        content_count=safe_int(user.get("statuses_count")),
        verified=1 if user.get("verified") else 0,
        raw=data,
    )
    return {"profiles": [row], "posts": [], "candidates": [], "platforms": ["twitter"]}


def normalize_posts(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    tweets = raw if isinstance(raw, list) else (data.get("tweets") or data.get("data") or data.get("items") or [])
    if isinstance(tweets, dict):
        tweets = tweets.get("tweets") or tweets.get("items") or []
    account_id = first_str(
        ctx.get("account_id"),
        (data.get("user") or {}).get("rest_id"),
        (data.get("user") or {}).get("id_str"),
    )
    # 工具常用 screen_name：用已入库 profile 反查 rest_id
    if not account_id:
        handle = first_str((ctx.get("tool_args") or {}).get("screen_name"))
        if handle and ctx.get("task_id"):
            from collect_01 import db

            prow = db.fetch_one(
                """
                SELECT account_id FROM collect_profiles
                WHERE task_id=%s AND platform='twitter' AND LOWER(account_handle)=LOWER(%s)
                LIMIT 1
                """,
                (ctx["task_id"], handle.lstrip("@")),
            )
            account_id = first_str((prow or {}).get("account_id"))
    account_id = account_id or "unknown"
    rows: List[Dict[str, Any]] = []
    for item in tweets if isinstance(tweets, list) else []:
        if not isinstance(item, dict):
            continue
        tid = first_str(item.get("id_str"), item.get("id"), item.get("tweet_id"))
        if not tid:
            continue
        rows.append(
            post_row(
                ctx,
                platform="twitter",
                account_id=str(account_id),
                content_id=str(tid),
                content_text=first_str(item.get("full_text"), item.get("text")),
                content_url=f"https://twitter.com/i/status/{tid}",
                published_at=published_at_from_item(item, "created_at", "date"),
                like_count=safe_int(item.get("favorite_count") if item.get("favorite_count") is not None else item.get("likes")),
                repost_count=safe_int(item.get("retweet_count") if item.get("retweet_count") is not None else item.get("retweets")),
                media=_extract_post_media(item),
                raw=item,
            )
        )
    return {"profiles": [], "posts": rows, "candidates": [], "platforms": ["twitter"] if rows else []}
