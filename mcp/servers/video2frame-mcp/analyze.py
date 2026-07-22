"""多模态帧分析 + 整段视频摘要（默认 Perplexity sonar）。"""

from __future__ import annotations

import base64
import io
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from PIL import Image

from config import vlm_config

logger = logging.getLogger(__name__)


def _encode_image_data_url(image_path: str, max_side: int = 1280, jpeg_quality: int = 85) -> str:
    """压成 JPEG data URL，减小 payload（比 PNG 快很多）。"""
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    scale = min(1.0, float(max_side) / max(w, h)) if max(w, h) > 0 else 1.0
    if scale < 1.0:
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=int(jpeg_quality), optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def _auth_headers(cfg: Dict[str, Any]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = (cfg.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _post_chat(
    *,
    api_url: str,
    payload: Dict[str, Any],
    headers: Dict[str, str],
    timeout: int,
) -> requests.Response:
    return requests.post(api_url, json=payload, headers=headers, timeout=timeout)


def analyze_single_frame(
    frame_path: str,
    *,
    api_url: Optional[str] = None,
    model: Optional[str] = None,
    prompt_text: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    cfg = vlm_config()
    api_url = api_url or cfg["api_url"]
    model = model or cfg["model"]
    prompt_text = prompt_text or cfg["prompt"]
    max_tokens = int(max_tokens or cfg["max_tokens"])
    timeout = int(cfg.get("timeout") or 120)

    if "perplexity.ai" in (api_url or "") and not cfg.get("api_key"):
        return {
            "frame": Path(frame_path).name,
            "frame_path": str(Path(frame_path).resolve()),
            "success": False,
            "latency_sec": 0,
            "error": "缺少 PERPLEXITY_API_KEY / HERMES_VIDEO_VLM_API_KEY",
        }

    data_url = _encode_image_data_url(frame_path)
    # OpenAI / Perplexity 兼容：image_url 为对象
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "stream": False,
    }
    started = time.time()
    name = Path(frame_path).name
    try:
        resp = _post_chat(
            api_url=api_url,
            payload=payload,
            headers=_auth_headers(cfg),
            timeout=timeout,
        )
        elapsed = round(time.time() - started, 2)
        if resp.status_code != 200:
            return {
                "frame": name,
                "frame_path": str(Path(frame_path).resolve()),
                "success": False,
                "latency_sec": elapsed,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
            }
        content = resp.json()["choices"][0]["message"]["content"]
        logger.info("帧分析成功 frame=%s latency=%ss", name, elapsed)
        return {
            "frame": name,
            "frame_path": str(Path(frame_path).resolve()),
            "success": True,
            "latency_sec": elapsed,
            "description": content,
        }
    except Exception as exc:
        return {
            "frame": name,
            "frame_path": str(Path(frame_path).resolve()),
            "success": False,
            "latency_sec": round(time.time() - started, 2),
            "error": str(exc),
        }


def analyze_frames(
    frame_paths: List[str],
    *,
    api_url: Optional[str] = None,
    model: Optional[str] = None,
    prompt_text: Optional[str] = None,
    max_tokens: Optional[int] = None,
    concurrency: Optional[int] = None,
) -> List[Dict[str, Any]]:
    cfg = vlm_config()
    workers = int(concurrency or cfg["concurrency"] or 3)
    results: List[Dict[str, Any]] = []
    total = len(frame_paths)
    logger.info("开始并行分析 frames=%s concurrency=%s model=%s", total, workers, model or cfg["model"])
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {
            pool.submit(
                analyze_single_frame,
                fp,
                api_url=api_url,
                model=model,
                prompt_text=prompt_text,
                max_tokens=max_tokens,
            ): fp
            for fp in frame_paths
        }
        done = 0
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as exc:
                fp = futs[fut]
                results.append(
                    {
                        "frame": Path(fp).name,
                        "frame_path": str(Path(fp).resolve()),
                        "success": False,
                        "latency_sec": 0,
                        "error": f"线程异常: {exc}",
                    }
                )
            done += 1
            if done == total or done % 2 == 0:
                ok = sum(1 for r in results if r.get("success"))
                logger.info("分析进度 %s/%s success=%s", done, total, ok)
    results.sort(key=lambda r: r.get("frame") or "")
    return results


def summarize_video_from_frame_texts(
    frame_items: List[Dict[str, Any]],
    *,
    api_url: Optional[str] = None,
    model: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """用已成功帧的描述生成整段视频摘要（纯文本，不再传图）。"""
    cfg = vlm_config()
    api_url = api_url or cfg["api_url"]
    model = model or cfg["model"]
    max_tokens = int(max_tokens or cfg["max_tokens"])
    timeout = int(cfg.get("timeout") or 120)
    lines = []
    for it in frame_items:
        if not it.get("success"):
            continue
        t = it.get("timestamp_sec")
        desc = (it.get("description") or "").strip()
        if not desc:
            continue
        lines.append(f"[t={t}s] {desc}")
    if not lines:
        return {"success": False, "text": "", "error": "无可用帧描述，无法生成整段摘要"}

    if "perplexity.ai" in (api_url or "") and not cfg.get("api_key"):
        return {"success": False, "text": "", "error": "缺少 PERPLEXITY_API_KEY"}

    body = cfg["summary_prompt"] + "\n\n" + "\n".join(lines[:80])
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": body}],
        "max_tokens": max_tokens,
        "stream": False,
    }
    try:
        resp = _post_chat(
            api_url=api_url,
            payload=payload,
            headers=_auth_headers(cfg),
            timeout=timeout,
        )
        if resp.status_code != 200:
            return {"success": False, "text": "", "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
        text = resp.json()["choices"][0]["message"]["content"]
        return {"success": True, "text": text, "error": None}
    except Exception as exc:
        logger.warning("整段视频摘要失败: %s", exc)
        return {"success": False, "text": "", "error": str(exc)}
