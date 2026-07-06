#!/usr/bin/env python3
"""
hermes-xa 专用 Twitter MCP 启动器：仅注入 HTTP 代理，启动上游 twikit-mcp。

- 不导入、不注册任何画像/persona 工具或 mandatory_output_contract
- 与 run_twitter_mcp.py（画像扩展版，供旧 Hermes 项目）分离

旧 Hermes 画像流程请使用 run_twitter_mcp.py 并设置 TWITTER_PERSONA_TOOLS=1
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from twitter_mcp._vendor.twikit import Client


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
        return client

    srv._get_client = _get_client  # type: ignore[method-assign]


def main() -> None:
    _configure_windows_stdio_utf8()
    _patch_get_client()
    from twitter_mcp.server import main as twikit_main

    twikit_main()


if __name__ == "__main__":
    main()
