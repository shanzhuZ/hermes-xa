"""从已有 OCR/Vision 工具输出回填分析结果（第一期不直接调模型）。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from image_pipeline import mysql_store

logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            obj = json.loads(text)
            return obj if isinstance(obj, dict) else {"text": text}
        except Exception:
            return {"text": text}
    return {}


def _extract_url(args: Dict[str, Any]) -> Optional[str]:
    for key in ("image_url", "imageUrl", "url", "path", "image_path", "file_path"):
        val = args.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
    # 部分 OCR 工具用 images 列表
    images = args.get("images") or args.get("image_urls")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, str):
            return first.strip()
        if isinstance(first, dict):
            return str(first.get("url") or first.get("path") or "").strip() or None
    return None


def _extract_text(result: Dict[str, Any], tool_name: str) -> str:
    for key in ("text", "ocr_text", "content", "description", "result", "answer", "output"):
        val = result.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # 嵌套 data
    data = result.get("data")
    if isinstance(data, dict):
        for key in ("text", "ocr_text", "content", "description"):
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    if isinstance(data, str) and data.strip():
        return data.strip()
    # 整段兜底
    raw = result.get("raw") or result.get("message")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    # OCR 工具名含 ocr 时尝试拼 texts
    if "ocr" in (tool_name or "").lower():
        texts = result.get("texts")
        if isinstance(texts, list):
            parts = [str(t).strip() for t in texts if t]
            if parts:
                return "\n".join(parts)
    return ""


def _url_loose_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # CDN 可能带签名参数，比 path 前缀
    a0 = a.split("?")[0]
    b0 = b.split("?")[0]
    if a0 == b0:
        return True
    # 一方包含另一方核心 path
    if len(a0) > 40 and (a0 in b or b0 in a):
        return True
    return False


def _index_tool_outputs(task_id: str) -> List[Dict[str, Any]]:
    rows = mysql_store.fetch_vision_tool_outputs(task_id)
    indexed = []
    for row in rows:
        args = _as_dict(row.get("tool_args"))
        result = _as_dict(row.get("tool_output"))
        url = _extract_url(args)
        tool_name = str(row.get("tool_name") or "")
        text = _extract_text(result, tool_name)
        indexed.append(
            {
                "id": row.get("id"),
                "tool_name": tool_name,
                "url": url,
                "text": text,
                "success": str(row.get("status") or "").lower() in {"success", "ok", "1", "true"},
                "result": result,
            }
        )
    return indexed


def _match_for_image(
    origin_url: str,
    indexed: List[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    ocr_text = None
    vision_text = None
    tool_output_id = None
    for item in indexed:
        url = item.get("url") or ""
        if url and not _url_loose_match(origin_url, url):
            continue
        # 无 URL 的工具输出不在此自动绑定，避免串图
        if not url:
            continue
        name = (item.get("tool_name") or "").lower()
        text = item.get("text") or ""
        if not text:
            continue
        if "ocr" in name:
            if not ocr_text:
                ocr_text = text
                tool_output_id = item.get("id")
        else:
            if not vision_text:
                vision_text = text
                tool_output_id = item.get("id")
    return ocr_text, vision_text, tool_output_id


def build_analysis_json(
    ocr_text: Optional[str],
    vision_text: Optional[str],
) -> Dict[str, Any]:
    summary = (vision_text or ocr_text or "").strip()
    if len(summary) > 500:
        summary = summary[:500] + "…"
    languages = []
    blob = f"{ocr_text or ''}{vision_text or ''}"
    if re.search(r"[\u4e00-\u9fff]", blob):
        languages.append("zh")
    if re.search(r"[A-Za-z]{3,}", blob):
        languages.append("en")
    return {
        "analysis_version": 1,
        "summary": summary,
        "imageType": "other",
        "languages": languages,
        "objects": [],
        "persons": [],
        "scene": "",
        "topics": [],
        "sensitiveSignals": [],
        "confidence": 0.5 if summary else 0.0,
        "source": "tool_output_backfill",
    }


def analyze_image_row(
    row: Dict[str, Any],
    indexed_outputs: Optional[List[Dict[str, Any]]] = None,
    force: bool = False,
) -> str:
    """
    分析单条图片记录。
    返回: completed / skipped / failed / unchanged
    """
    image_id = row["image_id"]
    if row.get("storage_status") != "stored":
        return "skipped"

    status = row.get("analyze_status")
    if status == "completed" and not force:
        return "unchanged"

    indexed = indexed_outputs
    if indexed is None:
        indexed = _index_tool_outputs(row["task_id"])

    try:
        mysql_store.mark_analyze_running(image_id)
        ocr_text, vision_text, tool_id = _match_for_image(row.get("origin_url") or "", indexed)
        if not ocr_text and not vision_text:
            mysql_store.mark_analyze_skipped(
                image_id,
                "暂无匹配的 OCR/Vision 工具输出；请由 Skill 调用分析工具后重跑 --force-analyze",
            )
            return "skipped"

        analysis = build_analysis_json(ocr_text, vision_text)
        mysql_store.mark_analyze_completed(
            image_id,
            ocr_text=ocr_text,
            vision_text=vision_text,
            analysis_json=analysis,
            tool_output_id=int(tool_id) if tool_id is not None else None,
        )
        return "completed"
    except Exception as exc:
        logger.exception("analyze failed image_id=%s", image_id)
        mysql_store.mark_analyze_failed(image_id, str(exc))
        return "failed"


def analyze_task(task_id: str, force: bool = False) -> Dict[str, int]:
    rows = mysql_store.list_images_for_task(task_id, storage_status="stored")
    indexed = _index_tool_outputs(task_id)
    stats = {"completed": 0, "skipped": 0, "failed": 0, "unchanged": 0}
    for row in rows:
        if row.get("analyze_status") == "completed" and not force:
            stats["unchanged"] += 1
            continue
        result = analyze_image_row(row, indexed_outputs=indexed, force=force)
        stats[result] = stats.get(result, 0) + 1
    return stats
