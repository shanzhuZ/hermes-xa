#!/usr/bin/env python3
"""
hermes-xa 专用 user-scanner MCP 启动器。

上游：https://github.com/kaifcodec/user-scanner
- Windows UTF-8
- 将 USER_SCANNER_PROXY / HTTPS_PROXY 注入工具参数 proxies
  （上游 get_proxy() 只认 set_proxy_manager，不读 HTTP_PROXY 环境变量）
- 默认开启 cross_scan（可用 USER_SCANNER_CROSS_SCAN=0 关闭）
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any


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


def _resolve_proxy_url() -> str | None:
    for key in ("USER_SCANNER_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def _ensure_proxy_env() -> None:
    proxy = _resolve_proxy_url()
    if not proxy:
        return
    os.environ.setdefault("HTTPS_PROXY", proxy)
    os.environ.setdefault("HTTP_PROXY", proxy)
    os.environ.setdefault("ALL_PROXY", proxy)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _patch_call_tool() -> None:
    """在 import server 之前打补丁，使上游 from handlers import call_tool 拿到包装版。"""
    import user_scanner.mcp.handlers as handlers

    log = logging.getLogger("user-scanner-mcp-hermes")
    orig = handlers.call_tool

    async def call_tool(name: str, arguments: dict | None) -> Any:
        args = dict(arguments or {})
        if name in ("scan_username", "scan_email"):
            # 代理：工具未传 proxies 时，从环境注入到 set_proxy_manager 路径
            if not args.get("proxies"):
                proxy = _resolve_proxy_url()
                if proxy:
                    args["proxies"] = [proxy]
                    log.info("已注入默认代理 proxies=[%s]", proxy)
                else:
                    log.warning(
                        "未配置 USER_SCANNER_PROXY/HTTPS_PROXY，扫描将直连（易 ERROR）"
                    )
            # cross_scan：未显式传参时按环境默认（默认开）
            if "cross_scan" not in args:
                args["cross_scan"] = _env_flag("USER_SCANNER_CROSS_SCAN", True)
                log.info("默认 cross_scan=%s", args["cross_scan"])
        return await orig(name, args)

    handlers.call_tool = call_tool  # type: ignore[method-assign]


def main() -> None:
    _configure_windows_stdio_utf8()
    _ensure_proxy_env()
    # 启动时打一条到 stderr，便于确认配置
    proxy = _resolve_proxy_url()
    cross = _env_flag("USER_SCANNER_CROSS_SCAN", True)
    print(
        f"[user-scanner-mcp] proxy={proxy or '(none)'} "
        f"default_cross_scan={cross}",
        file=sys.stderr,
    )
    _patch_call_tool()
    from user_scanner.mcp.server import main as upstream_main

    upstream_main()


if __name__ == "__main__":
    main()
