"""写报核验用文本 LLM（与图片 VLM 共用 API 环境变量）。"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


def _load_env() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    home = Path(os.environ.get("HERMES_HOME", "").strip() or root)
    env_path = home / ".env"
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


def llm_config() -> Dict[str, Any]:
    _load_env()
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
        "max_tokens": int(os.environ.get("HERMES_STREAM_LLM_MAX_TOKENS", "1600")),
        "timeout": int(os.environ.get("HERMES_STREAM_LLM_TIMEOUT", "120")),
    }


def chat_text(prompt: str, *, max_tokens: Optional[int] = None) -> Dict[str, Any]:
    """
    纯文本 chat completions。
    返回: {success, text|error, latency_sec}
    """
    cfg = llm_config()
    api_url = cfg["api_url"]
    model = cfg["model"]
    api_key = (cfg.get("api_key") or "").strip()
    timeout = int(cfg.get("timeout") or 120)
    tokens = int(max_tokens or cfg["max_tokens"])

    if not (prompt or "").strip():
        return {"success": False, "latency_sec": 0, "error": "empty_prompt"}
    if "perplexity.ai" in (api_url or "") and not api_key:
        return {
            "success": False,
            "latency_sec": 0,
            "error": "缺少 PERPLEXITY_API_KEY / HERMES_VIDEO_VLM_API_KEY",
        }

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": tokens,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    started = time.time()
    try:
        resp = requests.post(
            api_url,
            json=payload,
            headers=headers,
            timeout=timeout,
            proxies={"http": None, "https": None},
        )
        elapsed = round(time.time() - started, 2)
        if resp.status_code != 200:
            return {
                "success": False,
                "latency_sec": elapsed,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
            }
        text = resp.json()["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            return {"success": False, "latency_sec": elapsed, "error": "empty_llm_content"}
        return {"success": True, "latency_sec": elapsed, "text": text.strip()}
    except Exception as exc:
        elapsed = round(time.time() - started, 2)
        logger.warning("llm chat 失败: %s", exc)
        return {"success": False, "latency_sec": elapsed, "error": str(exc)[:500]}
