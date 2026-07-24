"""Apify Actor / dataset → profile 或 posts（按上下文推断）。"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional

from collect_01.normalizers.base import (
    first_str,
    parse_fuzzy_count,
    post_row,
    profile_row,
    published_at_from_item,
    safe_int,
)

# Apify Actor 工具 → 平台（与 phases.APIFY_TOOL_PLATFORM 一致）
APIFY_TOOL_PLATFORM: Dict[str, str] = {
    "mcp_apify_apify__instagram_scraper": "instagram",
    "mcp_apify_clockworks__tiktok_scraper": "tiktok",
    "mcp_apify_vujeen__telegram_channel_scraper": "telegram",
    "mcp_apify_headlessagent__facebook_profile_post_scraper": "facebook",
    "mcp_apify_knotless_cadence__github_profile_scraper": "github",
}
def resolve_apify_platform_hint(
    task_id: str,
    before_output_id: Optional[int] = None,
    dataset_id: Optional[str] = None,
) -> str:
    """从库中推断 get_dataset_items 对应平台。

    优先按 datasetId 反查产出该 dataset 的 Actor 工具；Hook 子进程无内存 hint。
    """
    from collect_01 import db

    ds = str(dataset_id or "").strip()
    if ds:
        rows = db.fetch_all(
            """
            SELECT tool_name, tool_output FROM hermes_tool_outputs
            WHERE task_id=%s AND status='success'
              AND tool_name LIKE 'mcp_apify_%%'
              AND tool_name NOT IN ('mcp_apify_get_dataset_items', 'mcp_apify_get_actor_run')
            ORDER BY id DESC
            """,
            (task_id,),
        )
        for row in rows:
            out = str(row.get("tool_output") or "")
            if ds in out:
                return str(row["tool_name"]).replace("mcp_apify_", "")

    sql = """
        SELECT tool_name FROM hermes_tool_outputs
        WHERE task_id=%s AND status='success'
          AND tool_name LIKE 'mcp_apify_%%'
          AND tool_name NOT IN ('mcp_apify_get_dataset_items', 'mcp_apify_get_actor_run')
    """
    args: list = [task_id]
    if before_output_id is not None:
        sql += " AND id < %s"
        args.append(before_output_id)
    sql += " ORDER BY id DESC LIMIT 1"
    row = db.fetch_one(sql, tuple(args))
    if not row:
        return ""
    return str(row["tool_name"]).replace("mcp_apify_", "")


def apify_platform_from_actor_tool(tool_name: str) -> str:
    return APIFY_TOOL_PLATFORM.get(tool_name or "", "")

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


def _item_is_unavailable(item: Dict[str, Any]) -> bool:
    """Apify 返回的 not_found / error，禁止当成功主页入库。"""
    err = str(item.get("error") or item.get("errorDescription") or "").strip().lower()
    if err and err not in {"", "null", "none"}:
        return True
    status = str(item.get("status") or "").strip().lower()
    return status in {"not_found", "error", "private", "unavailable", "denied"}


def _apify_collect_outcome(items: List[Any], profiles: List[Any], posts: List[Any]) -> str:
    """ok | not_found | empty — 供 03 步骤状态机区分「账号不存在」与「解析/空数据」。"""
    if profiles or posts:
        return "ok"
    if not items:
        return "empty"
    saw_unavailable = False
    for item in items:
        if not isinstance(item, dict):
            continue
        if _item_is_unavailable(item):
            saw_unavailable = True
            continue
        if str(item.get("type") or "").lower() == "profile":
            if not first_str(item.get("id"), item.get("url"), item.get("name")):
                return "empty"
    # 全是 not_found/error 壳 → 账号不可用；否则空解析
    return "not_found" if saw_unavailable else "empty"


def apify_fail_message(platform: str, collect_outcome: str) -> str:
    """dataset 成功但无主页时的步骤文案（避免「未入库」误导）。"""
    plat = platform or "apify"
    outcome = (collect_outcome or "empty").strip().lower()
    if outcome == "not_found":
        return f"{plat} 账号不存在或不可用"
    if outcome == "empty":
        return f"{plat} dataset 无可解析主页"
    return f"{plat} Apify 已拉取 dataset 但未得到主页"


def normalize_dataset_items(raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    items = data.get("items") or data.get("data") or []
    if not isinstance(items, list):
        items = []
    platform = _guess_platform(ctx, items)
    empty = {
        "profiles": [],
        "posts": [],
        "candidates": [],
        "platforms": [],
        "collect_outcome": "empty",
        "raw_item_count": 0,
    }
    if not platform:
        empty["raw_item_count"] = len(items) if isinstance(items, list) else 0
        return empty

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
    return {
        "profiles": profiles,
        "posts": posts,
        "candidates": [],
        "platforms": plats,
        "collect_outcome": _apify_collect_outcome(items, profiles, posts),
        "raw_item_count": len(items),
    }


def _guess_platform(ctx: Dict[str, Any], items: List[Any]) -> str:
    hint = str(ctx.get("platform_hint") or "").lower()
    for key in _PLATFORM_FROM_CTX:
        if key in hint:
            return key
    if items and isinstance(items[0], dict):
        sample = items[0]
        sample_blob = " ".join(
            str(sample.get(k) or "") for k in ("url", "displayUrl", "inputUrl", "type")
        ).lower()
        if (
            "ownerUsername" in sample
            or "instagram" in sample_blob
            or "cdninstagram" in sample_blob
            or (
                sample.get("caption") is not None
                and ("likesCount" in sample or "commentsCount" in sample or "displayUrl" in sample)
            )
        ):
            return "instagram"
        if "authorMeta" in sample or "tiktok" in str(sample.get("webVideoUrl", "")).lower():
            return "tiktok"
        item_type = str(sample.get("type") or "").lower()
        if (
            item_type == "channel"
            or "channelUsername" in sample
            or "t.me/" in str(sample.get("url", "")).lower()
            or sample.get("message")
        ):
            return "telegram"
        if item_type == "profile" and (
            "profile_intro_text" in sample
            or "facebook.com" in str(sample.get("url", "")).lower()
            or "profile_type" in sample
        ):
            return "facebook"
        if item_type == "post" and ("post_id" in sample or "facebook.com" in str(sample.get("url", "")).lower()):
            return "facebook"
        if "username" in sample and ("repos" in sample or "publicRepos" in sample):
            return "github"
    return ""


def _normalize_published_at(value: Any) -> Optional[str]:
    """兼容旧调用；统一走 base.normalize_published_at。"""
    from collect_01.normalizers.base import normalize_published_at

    return normalize_published_at(value)


def _short_content_url(url: Optional[str], limit: int = 512) -> Optional[str]:
    """CDN 签名 URL 极长，截断避免 content_url 列溢出；完整地址仍在 raw_json。"""
    text = first_str(url)
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _instagram_content_id(item: Dict[str, Any]) -> Optional[str]:
    """发文 content_id：优先 id/shortCode；Agent 常只拉 caption 等字段时用 URL/哈希兜底。"""
    pid = first_str(item.get("id"), item.get("shortCode"), item.get("code"), item.get("shortcode"))
    if pid:
        return str(pid)
    url = first_str(item.get("url"), item.get("displayUrl"), item.get("imageUrl"), item.get("videoUrl"))
    if url:
        m = re.search(r"instagram\.com/(?:p|reel|tv)/([^/?#]+)", url, re.I)
        if m:
            return m.group(1)
        # CDN 文件名常含 media id：375594674_18386609008061743_xxx.jpg
        m2 = re.search(r"/(\d{5,}_\d{5,}[^/?#]*)", url)
        if m2:
            return m2.group(1).split(".")[0]
    cap = first_str(item.get("caption")) or ""
    ts = first_str(item.get("timestamp"), item.get("takenAt"), item.get("taken_at")) or ""
    if not cap and not ts and not url:
        return None
    digest = hashlib.sha1(f"{cap}|{ts}|{url or ''}".encode("utf-8", errors="ignore")).hexdigest()[:20]
    return f"ig_{digest}"


def _instagram_account_id(ctx: Dict[str, Any], item: Dict[str, Any], fallback: str = "") -> str:
    username = first_str(item.get("ownerUsername"), item.get("username"), item.get("ownerId"))
    if username:
        return username
    hint = first_str(ctx.get("account_id"), ctx.get("account_handle"))
    if hint:
        return hint
    args = ctx.get("tool_args") if isinstance(ctx.get("tool_args"), dict) else {}
    for key in ("username", "user_id", "directUrls", "directUrl"):
        val = args.get(key)
        if isinstance(val, list) and val:
            val = val[0]
        text = first_str(val)
        if not text:
            continue
        m = re.search(r"instagram\.com/([^/?#]+)", text, re.I)
        if m and m.group(1).lower() not in {"p", "reel", "tv", "stories"}:
            return m.group(1)
        if "/" not in text and " " not in text:
            return text
    return fallback or "unknown"


def _instagram_items(ctx: Dict[str, Any], items: List[Any]):
    profiles: List[Dict[str, Any]] = []
    posts: List[Dict[str, Any]] = []
    default_user = ""
    for item in items:
        if not isinstance(item, dict):
            continue
        if _item_is_unavailable(item):
            continue
        username = first_str(item.get("ownerUsername"), item.get("username"))
        # 有效主页须有实质字段，禁止仅 username 的 not_found 壳入库
        has_signal = any(
            item.get(k) not in (None, "", [], {})
            for k in ("biography", "followersCount", "fullName", "ownerFullName", "postsCount", "id")
        )
        if username and not profiles and has_signal:
            default_user = username
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
        # 发文行：Agent 常只请求 caption/timestamp/likes… 无 id/shortCode，仍须入库
        is_postish = any(
            item.get(k) not in (None, "", [], {})
            for k in ("caption", "displayUrl", "timestamp", "likesCount", "commentsCount", "type", "shortCode", "id")
        ) and not has_signal
        # 同时有主页字段与 caption 的 latestPosts 混排：有 caption/displayUrl 也当发文
        has_post_body = item.get("caption") is not None or first_str(
            item.get("displayUrl"), item.get("url"), item.get("shortCode"), item.get("id")
        )
        if not has_post_body and not is_postish:
            continue
        # 纯主页壳（无发文信号）跳过
        if has_signal and item.get("caption") is None and not first_str(item.get("displayUrl"), item.get("shortCode")):
            continue
        pid = _instagram_content_id(item)
        if not pid:
            continue
        account_id = _instagram_account_id(ctx, item, fallback=default_user)
        posts.append(
            post_row(
                ctx,
                platform="instagram",
                account_id=account_id,
                content_id=str(pid),
                content_text=first_str(item.get("caption")) or "",
                content_url=_short_content_url(
                    first_str(item.get("url"), item.get("displayUrl"))
                ),
                published_at=published_at_from_item(item, "timestamp", "takenAt"),
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
        if _item_is_unavailable(item):
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
                    published_at=published_at_from_item(item, "createTime", "create_time"),
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
        if _item_is_unavailable(item):
            continue
        channel = first_str(
            item.get("channelUsername"),
            item.get("username"),
            item.get("channelName"),
            channel,
        )
        if channel and not profiles:
            profiles.append(
                profile_row(
                    ctx,
                    platform="telegram",
                    account_id=channel,
                    account_handle=channel,
                    display_name=first_str(item.get("channelName"), item.get("username")),
                    bio=first_str(item.get("channelDescription")),
                    profile_url=first_str(item.get("url"), f"https://t.me/{channel}"),
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
                    content_url=first_str(item.get("url")),
                    published_at=published_at_from_item(item, "date", "datetime", "time"),
                    view_count=safe_int(item.get("views")),
                    like_count=safe_int(item.get("reactions")),
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
            if _item_is_unavailable(item):
                continue
            account_id = first_str(item.get("id"), _facebook_handle_from_url(str(item.get("url") or "")))
            # 空壳 profile（id/url/name 全空）不入库，交给 collect_outcome=empty
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
                published_at=published_at_from_item(
                    item, "timestamp", "time", "created_time", "publish_time"
                ),
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
                    published_at=published_at_from_item(
                        repo, "createdAt", "pushedAt", "updatedAt", "created_at"
                    ),
                    like_count=safe_int(repo.get("stars")),
                    comment_count=safe_int(repo.get("openIssues")),
                    repost_count=safe_int(repo.get("forks")),
                    raw=repo,
                )
            )
    return profiles, posts
