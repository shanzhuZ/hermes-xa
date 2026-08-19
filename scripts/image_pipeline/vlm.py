"""图片直连 VLM（无 Agent OCR/Vision 工具输出时的回退）。

复用视频抽帧同一套环境变量：
  HERMES_VIDEO_VLM_API_URL / HERMES_VIDEO_VLM_MODEL / HERMES_VIDEO_VLM_API_KEY
  或 PERPLEXITY_API_KEY
"""

from __future__ import annotations

import base64
import io
import logging
import time
from typing import Any, Dict, Optional

from image_pipeline.config import vlm_config
from image_pipeline.proxy_http import requests_post_proxy_fallback

logger = logging.getLogger(__name__)


def _encode_bytes_data_url(content: bytes, mime_type: str = "image/jpeg") -> str:
    """压成 JPEG data URL，减小 payload。"""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(content)).convert("RGB")
        w, h = img.size
        max_side = 1280
        scale = min(1.0, float(max_side) / max(w, h)) if max(w, h) > 0 else 1.0
        if scale < 1.0:
            img = img.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64}"
    except Exception:
        mime = (mime_type or "image/jpeg").split(";")[0].strip() or "image/jpeg"
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        b64 = base64.b64encode(content).decode("utf-8")
        return f"data:{mime};base64,{b64}"


def analyze_image_bytes(
    content: bytes,
    *,
    mime_type: str = "image/jpeg",
    prompt_text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    调用多模态 chat completions。
    返回: {success, description|error, latency_sec}
    """
    cfg = vlm_config()
    api_url = cfg["api_url"]
    model = cfg["model"]
    prompt = prompt_text or cfg["prompt"]
    max_tokens = int(cfg["max_tokens"])
    timeout = int(cfg.get("timeout") or 120)
    api_key = (cfg.get("api_key") or "").strip()

    if not content:
        return {"success": False, "latency_sec": 0, "error": "empty_image_bytes"}
    if "perplexity.ai" in (api_url or "") and not api_key:
        return {
            "success": False,
            "latency_sec": 0,
            "error": "缺少 PERPLEXITY_API_KEY / HERMES_VIDEO_VLM_API_KEY",
        }

    data_url = _encode_bytes_data_url(content, mime_type)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    started = time.time()
    try:
        # 先 Clash/HTTPS_PROXY，连接失败再直连（阿里云直连 pplx 常超时）
        resp = requests_post_proxy_fallback(
            api_url,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        elapsed = round(time.time() - started, 2)
        if resp.status_code != 200:
            return {
                "success": False,
                "latency_sec": elapsed,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
            }
        content_text = resp.json()["choices"][0]["message"]["content"]
        if not isinstance(content_text, str) or not content_text.strip():
            return {"success": False, "latency_sec": elapsed, "error": "empty_vlm_content"}
        logger.info("图片 VLM 分析成功 latency=%ss", elapsed)
        return {
            "success": True,
            "latency_sec": elapsed,
            "description": content_text.strip(),
        }
    except Exception as exc:
        elapsed = round(time.time() - started, 2)
        logger.warning("图片 VLM 分析失败: %s", exc)
        return {"success": False, "latency_sec": elapsed, "error": str(exc)[:500]}
