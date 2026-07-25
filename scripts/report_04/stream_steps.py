"""04 步骤5（4.1）子节点：文本核验 / 图片核验 收口与详情辅助。"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

from report_04.gates import get_step_status
from report_04.phases import (
    STREAM_IMAGE_STEP_KEY,
    STREAM_PARENT_STEP_KEY,
    STREAM_TEXT_CONCLUSION_MARKER,
    STREAM_TEXT_STEP_KEY,
)

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"completed", "failed", "skipped"})
_IMAGE_JOB_LOCK = threading.Lock()
_IMAGE_JOBS: Dict[str, bool] = {}


def ensure_stream_child_steps(store: Any, task_id: str) -> None:
    """确保 4.1.1 / 4.1.2 存在（旧任务兼容）。"""
    store.ensure_step_row(task_id, STREAM_PARENT_STEP_KEY)
    store.ensure_step_row(task_id, STREAM_TEXT_STEP_KEY)
    store.ensure_step_row(task_id, STREAM_IMAGE_STEP_KEY)


def rollup_step5_parent(store: Any, task_id: str) -> bool:
    """两子都终态 → 父壳 completed，并尝试进步骤6。"""
    ensure_stream_child_steps(store, task_id)
    t = get_step_status(task_id, STREAM_TEXT_STEP_KEY)
    i = get_step_status(task_id, STREAM_IMAGE_STEP_KEY)
    if t not in _TERMINAL or i not in _TERMINAL:
        # 父壳保持 running
        if get_step_status(task_id, STREAM_PARENT_STEP_KEY) == "pending":
            store.set_step_status(
                task_id,
                STREAM_PARENT_STEP_KEY,
                "running",
                message=f"核验中 文本={t or 'pending'} 图片={i or 'pending'}",
            )
        return False
    parent = get_step_status(task_id, STREAM_PARENT_STEP_KEY)
    if parent not in _TERMINAL:
        store.set_step_status(
            task_id,
            STREAM_PARENT_STEP_KEY,
            "completed",
            message=f"文本核验 {t}，图片核验 {i}",
        )
    if get_step_status(task_id, "step6_validated") != "completed":
        try:
            store.run_validated_accounts(task_id)
        except Exception as exc:
            logger.warning("rollup 后 run_validated 失败 task=%s: %s", task_id, exc)
    return True


def parse_text_conclusion(assistant: str) -> Optional[str]:
    text = (assistant or "").strip()
    if not text:
        return None
    marker = STREAM_TEXT_CONCLUSION_MARKER
    idx = text.find(marker)
    if idx < 0:
        return None
    body = text[idx + len(marker) :].strip()
    if not body:
        body = text[idx:].strip()
    return body[:20000] if body else None


def apply_text_conclusion_from_assistant(store: Any, task_id: str, assistant: str) -> bool:
    """解析 [文本核验结论] → 写入 4.1.1 payload 并 completed。"""
    conclusion = parse_text_conclusion(assistant)
    if not conclusion:
        return False
    ensure_stream_child_steps(store, task_id)
    cur = get_step_status(task_id, STREAM_TEXT_STEP_KEY)
    payload = {"conclusion": conclusion, "source": "agent_marker"}
    # 合并已有比对统计
    try:
        from collect_01 import db
        import json as _json

        row = db.fetch_one(
            "SELECT payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
            (task_id, STREAM_TEXT_STEP_KEY),
        )
        existing = _json.loads((row or {}).get("payload_json") or "{}")
        if isinstance(existing, dict):
            for k in ("matched", "total", "text_compare_done"):
                if k in existing:
                    payload[k] = existing[k]
    except Exception:
        pass
    if cur in {"completed", "skipped"}:
        store.set_step_status(
            task_id,
            STREAM_TEXT_STEP_KEY,
            cur,
            message="文本核验结论已更新",
            payload=payload,
            touch_updated_at=False,
        )
    else:
        store.set_step_status(
            task_id,
            STREAM_TEXT_STEP_KEY,
            "completed",
            message="文本流核验结论已入库",
            payload=payload,
        )
    rollup_step5_parent(store, task_id)
    return True


def start_step5_image_pipeline(store: Any, task_id: str) -> None:
    """进入核验后后台跑 image_pipeline，完成后收口 4.1.2。"""
    ensure_stream_child_steps(store, task_id)
    cur = get_step_status(task_id, STREAM_IMAGE_STEP_KEY)
    if cur in _TERMINAL:
        rollup_step5_parent(store, task_id)
        return
    with _IMAGE_JOB_LOCK:
        if _IMAGE_JOBS.get(task_id):
            return
        _IMAGE_JOBS[task_id] = True

    store.set_step_status(
        task_id,
        STREAM_IMAGE_STEP_KEY,
        "running",
        message="图片资产入库与分析中",
    )
    if get_step_status(task_id, STREAM_PARENT_STEP_KEY) == "pending":
        store.set_step_status(
            task_id,
            STREAM_PARENT_STEP_KEY,
            "running",
            message="文本/图片流核查中",
        )

    def _work() -> None:
        try:
            from report_04.image_assets import count_stored_images, run_image_pipeline_for_report

            # 核验阶段：尽量跑完分析；已有 stored 也允许 force-analyze 回填
            summary = run_image_pipeline_for_report(
                task_id,
                force_analyze=True,
                skip_if_stored=False,
                timeout_sec=300,
            )
            stored = count_stored_images(task_id)
            if summary is None and stored <= 0:
                # 区分「无图」与「失败」：再发现一次候选数
                discovered = 0
                try:
                    from image_pipeline.discover import discover_images

                    discovered = len(discover_images(task_id) or [])
                except Exception:
                    discovered = -1
                if discovered == 0:
                    store.set_step_status(
                        task_id,
                        STREAM_IMAGE_STEP_KEY,
                        "skipped",
                        message="无图可核验，已跳过",
                        payload={"discovered": 0, "stored": 0},
                    )
                else:
                    store.set_step_status(
                        task_id,
                        STREAM_IMAGE_STEP_KEY,
                        "failed",
                        message="图片管线失败或超时且无已入库图片",
                        payload={"discovered": discovered, "stored": stored},
                    )
            elif stored <= 0 and isinstance(summary, dict) and int(summary.get("discovered") or 0) == 0:
                store.set_step_status(
                    task_id,
                    STREAM_IMAGE_STEP_KEY,
                    "skipped",
                    message="无图可核验，已跳过",
                    payload=summary if isinstance(summary, dict) else {"stored": 0},
                )
            elif stored > 0:
                analyzed = int((summary or {}).get("analyzed") or 0) if isinstance(summary, dict) else 0
                analyze_skipped = (
                    int((summary or {}).get("analyzeSkipped") or 0) if isinstance(summary, dict) else 0
                )
                analyze_failed = (
                    int((summary or {}).get("analyzeFailed") or 0) if isinstance(summary, dict) else 0
                )
                payload = summary if isinstance(summary, dict) else {"stored": stored}
                # 有入库图但 0 条分析成功 → 不能当 completed（前端无 visionText）
                if analyzed <= 0:
                    store.set_step_status(
                        task_id,
                        STREAM_IMAGE_STEP_KEY,
                        "failed",
                        message=(
                            f"图片已入库但分析未产出 analyzed=0 "
                            f"skipped={analyze_skipped} failed={analyze_failed}"
                        ),
                        payload=payload,
                    )
                else:
                    # 视觉分析完成后做图片流对比（写入 identity_streams + 对比结论）
                    compare: Dict[str, Any] = {}
                    try:
                        from report_04.stream_verify import run_image_model_compare

                        compare = run_image_model_compare(task_id) or {}
                    except Exception as exc:
                        logger.warning("图片流模型比对失败 task=%s: %s", task_id, exc)
                        compare = {"ok": False, "error": str(exc)[:300]}
                    if isinstance(payload, dict):
                        payload = dict(payload)
                        payload["compare"] = compare
                        if compare.get("conclusion"):
                            payload["compareConclusion"] = compare.get("conclusion")
                            payload["modelAnalysis"] = compare.get("modelAnalysis") or compare.get(
                                "conclusion"
                            )
                    store.set_step_status(
                        task_id,
                        STREAM_IMAGE_STEP_KEY,
                        "completed",
                        message=f"图片核验完成 stored={stored} analyzed={analyzed}",
                        payload=payload,
                    )
            else:
                store.set_step_status(
                    task_id,
                    STREAM_IMAGE_STEP_KEY,
                    "failed",
                    message="图片管线未产出可渲染图片",
                    payload=summary if isinstance(summary, dict) else {},
                )
        except Exception as exc:
            logger.exception("4.1.2 图片管线异常 task=%s", task_id)
            store.set_step_status(
                task_id,
                STREAM_IMAGE_STEP_KEY,
                "failed",
                message=str(exc)[:500],
            )
        finally:
            with _IMAGE_JOB_LOCK:
                _IMAGE_JOBS.pop(task_id, None)
            try:
                rollup_step5_parent(store, task_id)
            except Exception as exc:
                logger.warning("4.1.2 后 rollup 失败 task=%s: %s", task_id, exc)

    threading.Thread(target=_work, name=f"step5-image-{task_id[:8]}", daemon=True).start()
