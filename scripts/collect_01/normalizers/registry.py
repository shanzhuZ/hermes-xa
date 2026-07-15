"""01 采集 — MCP 工具路由到 normalizer。"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from collect_01.normalizers.base import unwrap_tool_payload, normalize_mcp_tool_name
from collect_01.normalizers import apify, bilibili, maigret, twitter, weibo, youtube

logger = logging.getLogger(__name__)

Handler = Callable[[Any, Dict[str, Any]], Dict[str, Any]]

# tool_name -> (handler, kind)  kind: profile | posts | candidates | noop
REGISTRY: Dict[str, Tuple[Handler, str]] = {
    "mcp_twitter_get_user_info": (twitter.normalize_profile, "profile"),
    "mcp_twitter_get_user_tweets": (twitter.normalize_posts, "posts"),
    "mcp_maigret_collect_accounts": (maigret.normalize_candidates, "candidates"),
    "mcp_maigret_search_username": (maigret.normalize_candidates, "candidates"),
    "mcp_maigret_search_usernames": (maigret.normalize_candidates, "candidates"),
    "mcp_youtube_get_channel_stats": (youtube.normalize_profile, "profile"),
    "mcp_youtube_analyze_channel_videos": (youtube.normalize_posts, "posts"),
    "mcp_apify_get_dataset_items": (apify.normalize_dataset_items, "mixed"),
    "mcp_apify_apify__instagram_scraper": (apify.normalize_actor_run, "noop"),
    "mcp_apify_clockworks__tiktok_scraper": (apify.normalize_actor_run, "noop"),
    "mcp_apify_vujeen__telegram_channel_scraper": (apify.normalize_actor_run, "noop"),
    "mcp_apify_headlessagent__facebook_profile_post_scraper": (apify.normalize_actor_run, "noop"),
    "mcp_apify_knotless_cadence__github_profile_scraper": (apify.normalize_actor_run, "noop"),
    "mcp_weibo_get_profile": (weibo.normalize_profile, "profile"),
    "mcp_weibo_get_feeds": (weibo.normalize_posts, "posts"),
    # Skill/phases 写作 get_user_feeds，实际 MCP 工具名为 get_feeds
    "mcp_weibo_get_user_feeds": (weibo.normalize_posts, "posts"),
    "mcp_weibo_search_content": (weibo.normalize_posts, "posts"),
    "mcp_bilibili_get_user_info": (bilibili.normalize_profile, "profile"),
}


def resolve(tool_name: str) -> Optional[Tuple[Handler, str]]:
    return REGISTRY.get(normalize_mcp_tool_name(tool_name))


def dispatch(tool_name: str, raw: Any, ctx: Dict[str, Any]) -> Dict[str, Any]:
    tool_name = normalize_mcp_tool_name(tool_name)
    entry = resolve(tool_name)
    if not entry:
        return {"profiles": [], "posts": [], "candidates": [], "platforms": []}
    handler, _kind = entry
    payload = unwrap_tool_payload(raw)
    try:
        return handler(payload, ctx)
    except Exception as exc:
        logger.exception("normalizer 失败 tool=%s: %s", tool_name, exc)
        return {"profiles": [], "posts": [], "candidates": [], "platforms": [], "error": str(exc)}
