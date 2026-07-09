"""Apify Actor / dataset → profile 或 posts（按上下文推断）。"""

from __future__ import annotations

from typing import Any, Dict, List

from collect_01.normalizers.base import first_str, parse_fuzzy_count, post_row, profile_row, safe_int

_PLATFORM_FROM_CTX = {
    "instagram": "instagram",
    "tiktok": "tiktok",
    "telegram": "telegram",
    "facebook": "facebook",
    "github": "github",
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
    elif platform == "facebook":
        profiles, posts = _facebook_items(ctx, items)
    elif platform == "github":
        profiles, posts = _github_items(ctx, items)

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
        item_type = str(sample.get("type") or "").lower()
        if item_type == "profile" and (
            "profile_intro_text" in sample or "facebook.com" in str(sample.get("url", "")).lower()
        ):
            return "facebook"
        if item_type == "post" and ("post_id" in sample or "facebook.com" in str(sample.get("url", "")).lower()):
            return "facebook"
        if "username" in sample and ("repos" in sample or "publicRepos" in sample):
            return "github"
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


def _facebook_handle_from_url(url: str) -> str:
    text = (url or "").strip().rstrip("/")
    if not text:
        return ""
    if "facebook.com/" in text:
        slug = text.split("facebook.com/", 1)[-1].split("/", 1)[0]
        if slug and slug not in {"profile.php", "people", "pages", "groups"}:
            return slug
    return ""


def _facebook_items(ctx: Dict[str, Any], items: List[Any]):
    profiles: List[Dict[str, Any]] = []
    posts: List[Dict[str, Any]] = []
    profile_ids = set()
    default_account_id = ""

    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").lower()
        if item_type == "profile":
            account_id = first_str(item.get("id"), _facebook_handle_from_url(str(item.get("url") or "")))
            if not account_id or account_id in profile_ids:
                continue
            profile_ids.add(account_id)
            default_account_id = account_id
            handle = _facebook_handle_from_url(str(item.get("url") or "")) or account_id
            profiles.append(
                profile_row(
                    ctx,
                    platform="facebook",
                    account_id=account_id,
                    account_handle=handle,
                    display_name=first_str(item.get("name")),
                    bio=first_str(item.get("profile_intro_text"), item.get("category")),
                    avatar_url=first_str(item.get("profile_picture")),
                    profile_url=first_str(item.get("url")),
                    follower_count=parse_fuzzy_count(item.get("followers")),
                    following_count=parse_fuzzy_count(item.get("following")),
                    verified=1 if item.get("verified") else 0,
                    raw=item,
                )
            )
            continue
        if item_type != "post":
            continue
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        account_id = first_str(author.get("id"), default_account_id) or "unknown"
        post_id = first_str(item.get("post_id"), item.get("id"))
        if not post_id:
            continue
        reactions = item.get("reactions_summary") if isinstance(item.get("reactions_summary"), dict) else {}
        comments = item.get("comments_summary") if isinstance(item.get("comments_summary"), dict) else {}
        posts.append(
            post_row(
                ctx,
                platform="facebook",
                account_id=account_id,
                content_id=str(post_id),
                content_text=first_str(item.get("message")),
                content_url=first_str(item.get("url")),
                like_count=safe_int(reactions.get("total_reactions")),
                comment_count=safe_int(comments.get("total_comments")),
                raw=item,
            )
        )
    return profiles, posts


def _github_items(ctx: Dict[str, Any], items: List[Any]):
    profiles: List[Dict[str, Any]] = []
    posts: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        username = first_str(item.get("username"))
        if not username:
            continue
        if not profiles:
            profiles.append(
                profile_row(
                    ctx,
                    platform="github",
                    account_id=username,
                    account_handle=username,
                    display_name=first_str(item.get("name")),
                    bio=first_str(item.get("bio"), item.get("company"), item.get("location")),
                    avatar_url=first_str(item.get("avatar")),
                    profile_url=first_str(item.get("profileUrl"), f"https://github.com/{username}"),
                    follower_count=safe_int(item.get("followers")),
                    following_count=safe_int(item.get("following")),
                    content_count=safe_int(item.get("publicRepos")),
                    raw=item,
                )
            )
        repos = item.get("repos") or []
        if not isinstance(repos, list):
            continue
        for repo in repos:
            if not isinstance(repo, dict):
                continue
            repo_name = first_str(repo.get("name"))
            if not repo_name:
                continue
            full_name = first_str(repo.get("fullName"), f"{username}/{repo_name}")
            posts.append(
                post_row(
                    ctx,
                    platform="github",
                    account_id=username,
                    content_id=full_name,
                    content_type="repo",
                    title=repo_name,
                    content_text=first_str(repo.get("description")),
                    content_url=first_str(repo.get("url"), f"https://github.com/{username}/{repo_name}"),
                    like_count=safe_int(repo.get("stars")),
                    comment_count=safe_int(repo.get("openIssues")),
                    repost_count=safe_int(repo.get("forks")),
                    raw=repo,
                )
            )
    return profiles, posts
