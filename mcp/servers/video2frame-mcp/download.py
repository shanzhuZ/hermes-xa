"""直链视频下载。"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from config import download_config

logger = logging.getLogger(__name__)


def _guess_ext(url: str, content_type: str) -> str:
    path = urlparse(url).path or ""
    suffix = Path(path).suffix.lower()
    if suffix in {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".flv", ".3gp", ".wmv"}:
        return suffix
    if content_type:
        ext = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if ext:
            return ext
    return ".mp4"


def download_direct_video(url: str, dest_path: Path) -> Dict[str, Any]:
    """下载直链视频到 dest_path。失败抛异常。"""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"仅支持 http(s) 直链: {url[:120]}")

    cfg = download_config()
    dest_path = dest_path.resolve()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # 固定临时文件名，避免无后缀时 with_suffix 纠错后指向错误 .part
    part_path = dest_path.parent / f"{dest_path.name}.part"

    headers = {"User-Agent": cfg["user_agent"]}
    last_err: Optional[Exception] = None
    for attempt in range(1, int(cfg["max_retries"]) + 1):
        try:
            with requests.get(
                url,
                headers=headers,
                stream=True,
                timeout=(cfg["connect_timeout"], cfg["read_timeout"]),
            ) as resp:
                resp.raise_for_status()
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if "html" in ctype and "video" not in ctype:
                    raise RuntimeError(f"响应像是网页而非视频: Content-Type={ctype}")

                max_bytes = int(cfg["max_bytes"])
                sha = hashlib.sha256()
                total = 0
                with open(part_path, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise RuntimeError(f"视频超过大小限制 {max_bytes} bytes")
                        sha.update(chunk)
                        f.write(chunk)

                if total <= 0:
                    raise RuntimeError("下载结果为空")

                final_path = dest_path
                if not final_path.suffix:
                    final_path = dest_path.with_suffix(_guess_ext(url, ctype))
                if final_path.exists():
                    final_path.unlink()
                part_path.replace(final_path)
                mime = (
                    ctype.split(";")[0].strip()
                    if ctype
                    else (mimetypes.guess_type(str(final_path))[0] or "video/mp4")
                )
                return {
                    "local_path": str(final_path),
                    "file_size": total,
                    "content_sha256": sha.hexdigest(),
                    "mime_type": mime,
                    "origin_url": url,
                }
        except Exception as exc:
            last_err = exc
            logger.warning("视频下载失败 attempt=%s/%s url=%s err=%s", attempt, cfg["max_retries"], url[:120], exc)
            if part_path.is_file():
                try:
                    part_path.unlink()
                except OSError:
                    pass
    raise RuntimeError(f"视频下载失败: {last_err}")


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", name or "video")
    return name[:120] or "video"
