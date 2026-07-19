"""使用 requests 下载图片并计算 SHA-256。"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from image_pipeline.config import download_config

logger = logging.getLogger(__name__)

_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
    (b"BM", "image/bmp"),
)


class DownloadError(RuntimeError):
    pass


def _guess_mime(content: bytes, content_type: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct.startswith("image/"):
        return ct
    for magic, mime in _MAGIC:
        if content.startswith(magic):
            if magic == b"RIFF" and len(content) >= 12 and content[8:12] != b"WEBP":
                continue
            return mime
    raise DownloadError(f"非图片内容或无法识别类型: content_type={content_type!r}")


def _referer_for(url: str, platform: Optional[str] = None) -> Optional[str]:
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return None
        base = f"{parsed.scheme}://{parsed.netloc}/"
    except Exception:
        return None
    plat = (platform or "").lower()
    host = parsed.netloc or ""
    if "instagram" in plat or "cdninstagram" in host:
        return "https://www.instagram.com/"
    if "facebook" in plat or "fbcdn" in host:
        return "https://www.facebook.com/"
    if "twitter" in plat or "twimg" in host:
        return "https://twitter.com/"
    if "weibo" in plat:
        return "https://weibo.com/"
    return base


def download_image(url: str, platform: Optional[str] = None) -> Dict[str, Any]:
    """
    下载图片。
    返回: {bytes, sha256, mime_type, file_size}
    """
    try:
        import requests
    except ImportError as exc:
        raise DownloadError("未安装 requests，请 pip install requests") from exc

    cfg = download_config()
    headers = {
        "User-Agent": cfg["user_agent"],
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    referer = _referer_for(url, platform)
    if referer:
        headers["Referer"] = referer

    last_err: Optional[Exception] = None
    max_retries = max(1, int(cfg["max_retries"]))
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                url,
                headers=headers,
                timeout=(cfg["connect_timeout"], cfg["read_timeout"]),
                stream=True,
                allow_redirects=True,
            )
            if resp.status_code != 200:
                raise DownloadError(f"HTTP {resp.status_code}")
            chunks = []
            total = 0
            max_bytes = int(cfg["max_bytes"])
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise DownloadError(f"超过最大大小限制 {max_bytes} bytes")
                chunks.append(chunk)
            content = b"".join(chunks)
            if not content:
                raise DownloadError("空响应体")
            mime = _guess_mime(content, resp.headers.get("Content-Type", ""))
            sha256 = hashlib.sha256(content).hexdigest()
            return {
                "bytes": content,
                "sha256": sha256,
                "mime_type": mime,
                "file_size": len(content),
            }
        except DownloadError as exc:
            last_err = exc
            logger.warning(
                "download fail attempt=%s/%s url=%s err=%s",
                attempt,
                max_retries,
                url[:120],
                exc,
            )
        except Exception as exc:
            last_err = DownloadError(str(exc))
            logger.warning(
                "download error attempt=%s/%s url=%s err=%s",
                attempt,
                max_retries,
                url[:120],
                exc,
            )
        if attempt < max_retries:
            time.sleep(min(2 * attempt, 6))

    raise DownloadError(str(last_err) if last_err else "下载失败")
