# -*- coding: utf-8 -*-
"""对外网 LLM/VLM 请求：优先走 Clash/HTTPS_PROXY，失败再直连。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
    "HERMES_VIDEO_PROXY",
)

# 连接类失败才切换路径；HTTP 4xx/5xx 由调用方处理，不在此回退
_RETRYABLE = (
    requests.exceptions.ProxyError,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.SSLError,
    requests.exceptions.ReadTimeout,
    requests.exceptions.Timeout,
)


def resolve_clash_proxy_url() -> Optional[str]:
    """读取环境中的代理地址（通常为本地 Clash）。"""
    for key in _PROXY_ENV_KEYS:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def iter_proxy_attempts() -> List[Tuple[str, Dict[str, Optional[str]]]]:
    """[(label, proxies), ...]：有代理则先 clash，再 direct。"""
    attempts: List[Tuple[str, Dict[str, Optional[str]]]] = []
    proxy = resolve_clash_proxy_url()
    if proxy:
        attempts.append(("clash", {"http": proxy, "https": proxy}))
    attempts.append(("direct", {"http": None, "https": None}))
    return attempts


def requests_post_proxy_fallback(
    url: str,
    *,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 120,
    **kwargs: Any,
) -> requests.Response:
    """
    POST：先经 Clash/系统代理，连接失败再直连。
    成功拿到 HTTP 响应（含 4xx/5xx）即返回，不再切换路径。
    """
    last_exc: Optional[BaseException] = None
    attempts = iter_proxy_attempts()
    for i, (label, proxies) in enumerate(attempts):
        try:
            logger.info("HTTP POST via %s", label)
            return requests.post(
                url,
                json=json,
                headers=headers,
                timeout=timeout,
                proxies=proxies,
                **kwargs,
            )
        except _RETRYABLE as exc:
            last_exc = exc
            if i + 1 < len(attempts):
                logger.warning("via %s 失败，改试下一路径: %s", label, exc)
            else:
                logger.warning("via %s 失败且无更多路径: %s", label, exc)
    assert last_exc is not None
    raise last_exc
