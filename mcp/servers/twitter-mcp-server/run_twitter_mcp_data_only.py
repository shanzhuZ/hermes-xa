#!/usr/bin/env python3
"""
hermes-xa 专用 Twitter MCP 启动器：仅注入 HTTP 代理，启动上游 twikit-mcp。

- 不导入、不注册任何画像/persona 工具或 mandatory_output_contract
- 与 run_twitter_mcp.py（画像扩展版，供旧 Hermes 项目）分离
- 对 get_user_by_screen_name / get_user_by_id 做一次瞬态重试（空错误/超时常见）

旧 Hermes 画像流程请使用 run_twitter_mcp.py 并设置 TWITTER_PERSONA_TOOLS=1
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# 新版 twitter_mcp.server 在 import 时强制读 OAuth 四元组；
# cookie/twikit 模式实际不使用它们，给占位即可。
os.environ.setdefault("CONSUMER_KEY", "cookie-mode")
os.environ.setdefault("CONSUMER_SECRET", "cookie-mode")
os.environ.setdefault("ACCESS_TOKEN", "cookie-mode")
os.environ.setdefault("ACCESS_TOKEN_SECRET", "cookie-mode")

from twitter_mcp._vendor.twikit import Client
from twitter_mcp._vendor.twikit.errors import NotFound, TooManyRequests


def _configure_windows_stdio_utf8() -> None:
    if os.name != "nt":
        return
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("FASTMCP_SHOW_SERVER_BANNER", "false")
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf:
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _resolve_proxy() -> str | None:
    for key in ("TWITTER_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def _is_transient_twikit_error(exc: BaseException) -> bool:
    if isinstance(exc, (NotFound, TooManyRequests)):
        return False
    msg = (str(exc) or "").strip().lower()
    if not msg:
        return True
    return any(
        k in msg
        for k in ("timeout", "timed out", "connection", "temporarily", "502", "503", "reset")
    )


def _wrap_with_one_retry(coro_fn):
    async def _wrapped(*args, **kwargs):
        try:
            return await coro_fn(*args, **kwargs)
        except Exception as first:
            if not _is_transient_twikit_error(first):
                raise
            await asyncio.sleep(1.5)
            return await coro_fn(*args, **kwargs)

    return _wrapped


def _patch_get_client() -> None:
    import twitter_mcp.server as srv

    proxy = _resolve_proxy()
    cookies_path = srv.COOKIES_PATH

    async def _get_client() -> Client:
        cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
        client = Client("en", proxy=proxy)
        client.set_cookies(
            {"auth_token": cookies["auth_token"], "ct0": cookies["ct0"]}
        )
        # 单次瞬态重试：缓解 get_user_info 空错误/超时（日志见 10～56s 空详情失败）
        client.get_user_by_screen_name = _wrap_with_one_retry(client.get_user_by_screen_name)  # type: ignore[method-assign]
        client.get_user_by_id = _wrap_with_one_retry(client.get_user_by_id)  # type: ignore[method-assign]
        return client

    srv._get_client = _get_client  # type: ignore[method-assign]


async def _fetch_tweets_paginated(client: Client, user_id: str, target_count: int) -> list:
    """翻页拉取用户时间线。X/twikit 单页常约 20 条，须 next() 才能凑满目标条数。"""
    target_count = max(1, int(target_count))
    # 单页请求量；过大也不会多返回，取 min(40, target) 与 twikit 默认接近
    page_size = min(40, target_count)
    page = await client.get_user_tweets(user_id, "Tweets", count=page_size)
    collected: list = list(page)
    current = page
    # 最多翻页次数：100 条约 5～6 页，留余量防死循环
    max_pages = max(8, (target_count + page_size - 1) // max(page_size, 1) + 2)
    pages = 1
    while len(collected) < target_count and pages < max_pages:
        if not getattr(current, "next_cursor", None):
            break
        try:
            nxt = await current.next()
        except Exception:
            break
        if not nxt or len(nxt) == 0:
            break
        collected.extend(list(nxt))
        current = nxt
        pages += 1
        # 轻微间隔，降低限流概率
        await asyncio.sleep(0.4)
    return collected[:target_count]


def _media_url_from_item(m: Any) -> str | None:
    """从 twikit media 对象提取可下载的图片/封面 URL。"""
    if isinstance(m, dict):
        for key in ("media_url_https", "media_url", "url"):
            u = m.get(key)
            if u and str(u).startswith("http"):
                return str(u)
        return None
    for key in ("media_url_https", "media_url", "url"):
        u = getattr(m, key, None)
        if u and str(u).startswith("http"):
            return str(u)
    return None


def _patch_get_user_tweets_media() -> None:
    """增强 get_user_tweets：附带 media_types / media_urls / has_video，并翻页凑满条数。"""
    import logging

    import twitter_mcp.server as srv
    from mcp.server.fastmcp.tools.base import Tool

    log = logging.getLogger("twitter-data-only")

    async def get_user_tweets(screen_name: str, count: int = 100) -> str:
        """Get recent tweets from a specific user（含媒体 URL，便于发文配图入库）。

        Args:
            screen_name: Twitter username (without @).
            count: Number of tweets to fetch（默认/下限 100；内部翻页凑满）。
        """
        # 业务下限 100；单页不够时自动翻页
        try:
            count = int(count or 100)
        except (TypeError, ValueError):
            count = 100
        count = max(100, count)
        client = await srv._get_client()
        user = await client.get_user_by_screen_name(screen_name)
        tweets = await _fetch_tweets_paginated(client, user.id, count)
        result = []
        for t in tweets:
            media_types: list[str] = []
            media_urls: list[str] = []
            media_items: list[dict[str, str]] = []
            has_video = False
            try:
                media = getattr(t, "media", None) or []
                for m in media:
                    mtype = getattr(m, "type", None) or (
                        m.get("type") if isinstance(m, dict) else None
                    )
                    mtype_s = str(mtype) if mtype else ""
                    if mtype_s:
                        media_types.append(mtype_s)
                        if mtype_s.lower() in {"video", "animated_gif"}:
                            has_video = True
                    url = _media_url_from_item(m)
                    if not url:
                        continue
                    # photo → media_urls；video/gif 封面也写入 media 供 image_pipeline 取 thumb
                    item: dict[str, str] = {"type": mtype_s or "photo", "url": url}
                    if mtype_s.lower() in {"video", "animated_gif"}:
                        item["thumb"] = url
                    else:
                        media_urls.append(url)
                    media_items.append(item)
            except Exception:
                pass
            result.append(
                {
                    "id": t.id,
                    "text": t.full_text,
                    "created_at": str(t.created_at),
                    "likes": t.favorite_count,
                    "retweets": t.retweet_count,
                    "media_types": media_types,
                    "media_urls": media_urls,
                    "media": media_items,
                    "has_media": bool(media_types),
                    "has_video": has_video,
                }
            )
        log.info(
            "get_user_tweets @%s target=%s got=%s",
            screen_name,
            count,
            len(result),
        )
        return srv._dumps(result)

    try:
        srv.mcp.remove_tool("get_user_tweets")
    except Exception as exc:
        log.warning("remove_tool get_user_tweets: %s", exc)
    try:
        srv.mcp.add_tool(get_user_tweets)
        log.info("已注册增强版 get_user_tweets（含 media_urls / has_video）")
    except Exception as exc:
        log.warning("add_tool get_user_tweets 失败: %s", exc)
        try:
            tool = Tool.from_function(get_user_tweets)
            srv.mcp._tool_manager._tools["get_user_tweets"] = tool
            log.info("已手动写入 ToolManager get_user_tweets")
        except Exception as exc2:
            log.warning("挂接 get_user_tweets 失败: %s", exc2)


def main() -> None:
    _configure_windows_stdio_utf8()
    _patch_get_client()
    _patch_get_user_tweets_media()
    from twitter_mcp.server import main as twikit_main

    twikit_main()


if __name__ == "__main__":
    main()
