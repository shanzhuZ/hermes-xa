"""
Maigret 代理兼容补丁。

根因：maigret 默认用 aiohttp + ProxyConnector 走代理时，大量站点报 Server disconnected；
仅带 tls_fingerprint 标签的站（如 Instagram）会走 curl_cffi，反而能通。
Clash 端口本身正常，浏览器/aiohttp 直连页面也可 200，但 maigret 的 aiohttp 代理链路不稳定。

对策：当设置了 --proxy 时，为所有站点启用 curl_cffi（浏览器 TLS 指纹），与 Instagram 同路径。

兼容说明：
- maigret<=0.5.x / 部分 0.6.x：checking.CURL_CFFI_AVAILABLE
- 较新 main：可能直接 import curl_cffi，不再暴露 CURL_CFFI_AVAILABLE
补丁必须用 getattr / 探测，禁止因缺属性导致 CLI 直接崩溃。
"""

from __future__ import annotations


def _curl_cffi_ready(checking) -> bool:
    """判断当前 maigret.checking 是否可用 curl_cffi 路径。"""
    flag = getattr(checking, "CURL_CFFI_AVAILABLE", None)
    if flag is True:
        return True
    if flag is False:
        return False
    # 新版本可能无 CURL_CFFI_AVAILABLE，但有 CurlCffiChecker
    if getattr(checking, "CurlCffiChecker", None) is not None:
        return True
    try:
        from curl_cffi.requests import AsyncSession  # noqa: F401

        return True
    except ImportError:
        return False


def apply_patches() -> None:
    import maigret.checking as checking

    if getattr(checking, "_MAIGRET_MCP_PATCHED", False):
        return
    checking._MAIGRET_MCP_PATCHED = True

    if not _curl_cffi_ready(checking):
        return

    _orig_make_site_result = getattr(checking, "make_site_result", None)
    if not callable(_orig_make_site_result):
        return

    def make_site_result(site, username, options, logger, *args, **kwargs):
        if options.get("proxy"):
            prot = list(getattr(site, "protection", None) or [])
            if "tls_fingerprint" not in prot:
                site.protection = prot + ["tls_fingerprint"]
        return _orig_make_site_result(site, username, options, logger, *args, **kwargs)

    checking.make_site_result = make_site_result
