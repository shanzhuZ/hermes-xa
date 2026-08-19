"""视频 MCP 配置：路径、下载限制、VLM 默认值。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict


def _repo_root() -> Path:
    # mcp/servers/video2frame-mcp/config.py → 仓库根
    return Path(__file__).resolve().parent.parent.parent.parent


def hermes_home() -> Path:
    val = (os.environ.get("HERMES_HOME") or "").strip()
    if val:
        return Path(val).resolve()
    return _repo_root()


def load_dotenv_if_present() -> None:
    env_path = hermes_home() / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def video_root_dir() -> Path:
    load_dotenv_if_present()
    raw = (os.environ.get("HERMES_VIDEO_LOCAL_DIR") or "").strip()
    if raw:
        return Path(raw).resolve()
    return hermes_home() / "data" / "video_bytes"


def download_config() -> Dict[str, Any]:
    load_dotenv_if_present()
    return {
        "connect_timeout": float(os.environ.get("HERMES_VIDEO_CONNECT_TIMEOUT", "15")),
        "read_timeout": float(os.environ.get("HERMES_VIDEO_READ_TIMEOUT", "300")),
        "max_bytes": int(os.environ.get("HERMES_VIDEO_MAX_BYTES", str(200 * 1024 * 1024))),
        "max_retries": int(os.environ.get("HERMES_VIDEO_MAX_RETRIES", "3")),
        "user_agent": os.environ.get(
            "HERMES_VIDEO_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ),
        # YouTube 反爬：cookies 文件或浏览器（chrome/edge）
        "ytdlp_cookies": (os.environ.get("HERMES_VIDEO_YTDLP_COOKIES") or "").strip(),
        "ytdlp_cookies_from_browser": (
            os.environ.get("HERMES_VIDEO_YTDLP_COOKIES_FROM_BROWSER") or ""
        ).strip(),
        "proxy": (
            os.environ.get("HERMES_VIDEO_PROXY")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or ""
        ).strip(),
    }


def vlm_config() -> Dict[str, Any]:
    """默认走 Perplexity sonar（与 config.yaml auxiliary.vision 一致）；可用环境变量覆盖回本地 32B。"""
    load_dotenv_if_present()
    api_key = (
        os.environ.get("HERMES_VIDEO_VLM_API_KEY")
        or os.environ.get("PERPLEXITY_API_KEY")
        or ""
    ).strip()
    return {
        "api_url": os.environ.get(
            "HERMES_VIDEO_VLM_API_URL",
            "https://api.perplexity.ai/chat/completions",
        ),
        "model": os.environ.get("HERMES_VIDEO_VLM_MODEL", "sonar"),
        "api_key": api_key,
        "prompt": os.environ.get(
            "HERMES_VIDEO_VLM_PROMPT",
            (
                "请用中文分析本帧画面。先完整描述正常可见内容（人物、动作、物体、文字、氛围），不要省略。"
                "再在末尾固定补充两行："
                "【地点】能识别则写具体场景/场所线索（如室内狭小房间、街景、店铺等），无法判断写「无法识别」；"
                "【涉华】有则说明依据（华人面孔/汉字标语/国旗国徽/中国场景或隐喻讽刺等），无则写「未见明显涉华因素」。"
            ),
        ),
        "summary_prompt": os.environ.get(
            "HERMES_VIDEO_VLM_SUMMARY_PROMPT",
            (
                "下面是同一视频按时间顺序的抽帧描述。请用中文做整段摘要："
                "1）保留正常内容概括（人物、情节、主题、关键画面），不要丢掉非涉华信息；"
                "2）单独归纳【地点】：能否识别主要拍摄/场景地点，依据是什么，无法识别请明确写；"
                "3）单独归纳【涉华】：是否存在涉华因素或涉华内涵（含讽刺、隐喻），有则说明，无则写「未见明显涉华因素」。"
                "输出顺序：内容概要 → 【地点】→【涉华】。"
            ),
        ),
        "max_tokens": int(os.environ.get("HERMES_VIDEO_VLM_MAX_TOKENS", "800")),
        # pplx 有速率限制，默认略保守
        "concurrency": int(os.environ.get("HERMES_VIDEO_VLM_CONCURRENCY", "3")),
        "timeout": int(os.environ.get("HERMES_VIDEO_VLM_TIMEOUT", "60")),
    }


def default_frame_interval_sec() -> float:
    load_dotenv_if_present()
    return float(os.environ.get("HERMES_VIDEO_FRAME_INTERVAL_SEC", "3"))


def max_analyze_duration_sec() -> float:
    """只分析视频前 N 秒（默认 120=前2分钟）。"""
    load_dotenv_if_present()
    return float(os.environ.get("HERMES_VIDEO_MAX_ANALYZE_SEC", "120"))


def suggested_max_videos_per_task() -> int:
    """文档建议上限；第一期不做硬限制。"""
    return 3
