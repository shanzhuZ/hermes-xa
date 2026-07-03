"""
Maigret 代理兼容补丁。

根因：maigret 默认用 aiohttp + ProxyConnector 走代理时，大量站点报 Server disconnected；
仅带 tls_fingerprint 标签的站（如 Instagram）会走 curl_cffi，反而能通。
Clash 端口本身正常，浏览器/aiohttp 直连页面也可 200，但 maigret 的 aiohttp 代理链路不稳定。

对策：当设置了 --proxy 时，为所有站点启用 curl_cffi（浏览器 TLS 指纹），与 Instagram 同路径。
"""

from __future__ import annotations


def apply_patches() -> None:
    import maigret.checking as checking

    if getattr(checking, "_MAIGRET_MCP_PATCHED", False):
        return
    checking._MAIGRET_MCP_PATCHED = True

    if not checking.CURL_CFFI_AVAILABLE:
        return

    _orig_make_site_result = checking.make_site_result

    def make_site_result(site, username, options, logger, *args, **kwargs):
        if options.get("proxy"):
            prot = list(getattr(site, "protection", None) or [])
            if "tls_fingerprint" not in prot:
                site.protection = prot + ["tls_fingerprint"]
        return _orig_make_site_result(site, username, options, logger, *args, **kwargs)

    checking.make_site_result = make_site_result
