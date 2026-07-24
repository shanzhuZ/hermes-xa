"""视频下载：http(s) 直链 + YouTube/平台页（yt-dlp）。"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from config import download_config

logger = logging.getLogger(__name__)

_YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "www.youtu.be",
    "music.youtube.com",
}


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", name or "video")
    return name[:120] or "video"


def is_youtube_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    if host in _YOUTUBE_HOSTS:
        return True
    return host.endswith(".youtube.com")


def needs_ytdlp(url: str) -> bool:
    """页面链接（非直链扩展名）走 yt-dlp。"""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return False
    if is_youtube_url(url):
        return True
    path = (urlparse(url).path or "").lower()
    if any(path.endswith(ext) for ext in (".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi", ".flv")):
        return False
    # Twitter/X / 微博等状态页
    host = (urlparse(url).hostname or "").lower()
    if any(
        h in host
        for h in (
            "twitter.com",
            "x.com",
            "t.co",
            "weibo.com",
            "weibo.cn",
            "tiktok.com",
            "instagram.com",
            "facebook.com",
            "fb.watch",
        )
    ):
        return True
    return False


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
                    "downloader": "direct",
                }
        except Exception as exc:
            last_err = exc
            logger.warning(
                "视频下载失败 attempt=%s/%s url=%s err=%s",
                attempt,
                cfg["max_retries"],
                url[:120],
                exc,
            )
            if part_path.is_file():
                try:
                    part_path.unlink()
                except OSError:
                    pass
    raise RuntimeError(f"视频下载失败: {last_err}")


def _ytdlp_cmd() -> list[str]:
    """优先本机 python -m yt_dlp，其次 PATH 上的 yt-dlp。"""
    try:
        import yt_dlp  # noqa: F401

        return [sys_executable(), "-m", "yt_dlp"]
    except ImportError:
        pass
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if exe:
        return [exe]
    raise RuntimeError("未安装 yt-dlp：请 pip install yt-dlp 或确保 yt-dlp 在 PATH")


def sys_executable() -> str:
    import sys

    return sys.executable


def download_with_ytdlp(
    url: str,
    dest_path: Path,
    *,
    max_duration_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """用 yt-dlp 下载页面视频（YouTube / Twitter 等）。

    max_duration_sec>0 时只下载前 N 秒（--download-sections），避免整片 20 分钟全量拉取。
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"仅支持 http(s): {url[:120]}")

    cfg = download_config()
    dest_path = dest_path.resolve()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    # yt-dlp 输出模板（扩展名由合并器决定）
    outtmpl = str(dest_path.parent / f"{dest_path.stem}.%(ext)s")
    section_end = float(max_duration_sec) if max_duration_sec and float(max_duration_sec) > 0 else 0.0

    def _build_cmd(*, use_cookies_file: bool, use_browser: bool) -> list:
        c = _ytdlp_cmd() + [
            "--no-playlist",
            "--no-warnings",
            # YouTube n-challenge 需要 JS runtime + yt-dlp-ejs（pip: yt-dlp[default]）
            "--js-runtimes",
            "node",
            "-f",
            "bv*[height<=720]+ba/b[height<=720]/b",
            "--merge-output-format",
            "mp4",
            "-o",
            outtmpl,
            "--socket-timeout",
            str(int(cfg["connect_timeout"])),
            "--retries",
            str(int(cfg["max_retries"])),
        ]
        # 只下前 N 秒，与抽帧/分析上限对齐
        if section_end > 0:
            c.extend(
                [
                    "--download-sections",
                    f"*0-{section_end:g}",
                    "--force-keyframes-at-cuts",
                ]
            )
        cookies_file = str(cfg.get("ytdlp_cookies") or "").strip()
        cookies_browser = str(cfg.get("ytdlp_cookies_from_browser") or "").strip()
        if use_cookies_file and cookies_file and Path(cookies_file).is_file():
            c.extend(["--cookies", cookies_file])
        elif use_browser and cookies_browser:
            c.extend(["--cookies-from-browser", cookies_browser])
        proxy = str(cfg.get("proxy") or "").strip()
        if proxy:
            c.extend(["--proxy", proxy])
        c.append(url)
        return c

    attempts = [
        ("plain", False, False),
        ("cookies_file", True, False),
        ("browser", False, True),
    ]
    last_err = ""
    ok = False
    for name, use_file, use_browser in attempts:
        if use_file and not str(cfg.get("ytdlp_cookies") or "").strip():
            continue
        if use_browser and not str(cfg.get("ytdlp_cookies_from_browser") or "").strip():
            continue
        cmd = _build_cmd(use_cookies_file=use_file, use_browser=use_browser)
        logger.info(
            "yt-dlp 下载(%s) section=%s: %s",
            name,
            f"0-{section_end:g}s" if section_end > 0 else "full",
            url[:160],
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(cfg["read_timeout"]) + 60,
        )
        if proc.returncode == 0:
            ok = True
            break
        last_err = (proc.stderr or proc.stdout or "").strip()[-800:]
        logger.warning("yt-dlp 尝试失败 mode=%s err=%s", name, last_err[:240])
    if not ok:
        raise RuntimeError(f"yt-dlp 失败: {last_err}")

    # 找刚下的文件
    candidates = sorted(
        dest_path.parent.glob(f"{dest_path.stem}.*"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    final: Optional[Path] = None
    for p in candidates:
        if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov", ".m4v"} and p.is_file():
            final = p
            break
    if final is None:
        raise RuntimeError("yt-dlp 完成但未找到输出文件")

    # 统一成 dest_path + 后缀
    if dest_path.suffix:
        target = dest_path
    else:
        target = dest_path.with_suffix(final.suffix)
    if final.resolve() != target.resolve():
        if target.exists():
            target.unlink()
        final.replace(target)
        final = target

    data = final.read_bytes()
    if len(data) > int(cfg["max_bytes"]):
        final.unlink(missing_ok=True)
        raise RuntimeError(f"视频超过大小限制 {cfg['max_bytes']} bytes")
    sha = hashlib.sha256(data).hexdigest()
    mime = mimetypes.guess_type(str(final))[0] or "video/mp4"
    return {
        "local_path": str(final),
        "file_size": len(data),
        "content_sha256": sha,
        "mime_type": mime,
        "origin_url": url,
        "downloader": "yt-dlp",
        "download_section_sec": section_end if section_end > 0 else None,
    }


def download_video(
    url: str,
    dest_path: Path,
    *,
    max_duration_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """自动选择直链或 yt-dlp。max_duration_sec 仅对 yt-dlp 生效（按时长截断下载）。"""
    url = (url or "").strip()
    if needs_ytdlp(url):
        return download_with_ytdlp(url, dest_path, max_duration_sec=max_duration_sec)
    try:
        return download_direct_video(url, dest_path)
    except Exception as direct_err:
        # 直链失败时，若像页面则回退 yt-dlp
        logger.warning("直链失败，尝试 yt-dlp: %s", direct_err)
        return download_with_ytdlp(url, dest_path, max_duration_sec=max_duration_sec)
