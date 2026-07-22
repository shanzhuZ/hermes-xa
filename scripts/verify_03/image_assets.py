"""03 账号核查：图片资产入库（复用 image_pipeline）。

两阶段时序：
1. 步骤2（step3_profiles）收口 → 后台 kickoff：只下载入库（skip_analyze），加速存图
2. 步骤4 图片流 Vision 完成后 / 成报前 ensure → 完整管线 + force-analyze，回填工具输出

完成判定（禁止「任意 1 条 stored 就算完成」）：
- 尚无 collect_images 行 → 存储未完成
- 存在 pending/downloading → 存储未完成
- 存在 stored 且 analyze 仍为 pending/running → 分析未完成（下载-only 后常见）
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_HOOK_TIMEOUT_SEC = 90


def count_stored_images(task_id: str) -> int:
    from collect_01.image_assets import count_stored_images as _count

    return _count(task_id)


def has_stored_images(task_id: str) -> bool:
    """是否至少有一张已存储（历史兼容；不要用作管线完成条件）。"""
    return count_stored_images(task_id) > 0


def _image_pipeline_stats(task_id: str) -> Dict[str, int]:
    from collect_01 import db

    row = db.fetch_one(
        """
        SELECT
          COUNT(*) AS total,
          SUM(CASE WHEN storage_status IN ('pending', 'downloading') THEN 1 ELSE 0 END) AS open_storage,
          SUM(
            CASE
              WHEN storage_status='stored'
               AND analyze_status IN ('pending', 'running')
              THEN 1 ELSE 0
            END
          ) AS open_analyze
        FROM collect_images
        WHERE task_id=%s
        """,
        (task_id,),
    ) or {}
    return {
        "total": int(row.get("total") or 0),
        "open_storage": int(row.get("open_storage") or 0),
        "open_analyze": int(row.get("open_analyze") or 0),
    }


def is_image_storage_done(task_id: str) -> bool:
    """下载入库是否已充分完成（步骤3后 kickoff 用）。"""
    if not task_id:
        return True
    stats = _image_pipeline_stats(task_id)
    if stats["total"] <= 0:
        return False
    return stats["open_storage"] <= 0


def is_image_pipeline_done(task_id: str) -> bool:
    """存储+分析是否均已充分完成（步骤4后 / 成报前用）。"""
    if not task_id:
        return True
    stats = _image_pipeline_stats(task_id)
    if stats["total"] <= 0:
        return False
    return stats["open_storage"] <= 0 and stats["open_analyze"] <= 0


def run_image_pipeline_for_verify(
    task_id: str,
    *,
    force_analyze: bool = True,
    skip_if_stored: bool = False,
    skip_analyze: bool = False,
    timeout_sec: Optional[int] = _HOOK_TIMEOUT_SEC,
) -> Optional[Dict[str, Any]]:
    """核查场景跑图片管线。失败只打日志，不抛出。"""
    if not task_id:
        return None
    from collect_01.image_assets import run_image_pipeline_after_posts

    def _run() -> Optional[Dict[str, Any]]:
        return run_image_pipeline_after_posts(
            task_id,
            force_analyze=force_analyze,
            skip_if_stored=skip_if_stored,
            skip_analyze=skip_analyze,
        )

    if timeout_sec is None or timeout_sec <= 0:
        try:
            return _run()
        except Exception as exc:
            logger.warning("03 图片管线失败 task=%s: %s", task_id, exc)
            return None

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run)
            try:
                return fut.result(timeout=timeout_sec)
            except FuturesTimeout:
                logger.warning(
                    "03 图片管线等待超时(%ss)，放弃阻塞 task=%s（不 fail 任务）",
                    timeout_sec,
                    task_id,
                )
                return None
    except Exception as exc:
        logger.warning("03 图片管线异常 task=%s: %s", task_id, exc)
        return None


def _start_bg(task_id: str, *, reason: str, skip_analyze: bool, force_analyze: bool) -> None:
    def _bg() -> None:
        try:
            logger.info(
                "03 图片管线后台启动 reason=%s skip_analyze=%s force_analyze=%s task=%s",
                reason or "-",
                skip_analyze,
                force_analyze,
                task_id,
            )
            run_image_pipeline_for_verify(
                task_id,
                force_analyze=force_analyze,
                skip_if_stored=False,
                skip_analyze=skip_analyze,
                timeout_sec=None,
            )
        except Exception as exc:
            logger.warning("03 图片管线后台失败 reason=%s task=%s: %s", reason or "-", task_id, exc)

    threading.Thread(
        target=_bg,
        name=f"verify03-img-{task_id[:8]}",
        daemon=True,
    ).start()


def kickoff_images_background(task_id: str, *, reason: str = "") -> None:
    """步骤2收口热路径：后台只下载入库，不做 analyze（等步骤4 Vision 后再回填）。"""
    if not task_id:
        return
    if is_image_storage_done(task_id):
        return
    _start_bg(
        task_id,
        reason=reason or "step3_profiles_completed",
        skip_analyze=True,
        force_analyze=False,
    )


def kickoff_images_after_vision(task_id: str, *, reason: str = "") -> None:
    """步骤4 图片流完成后：后台完整管线 + force-analyze，回填 Vision/OCR 工具输出。"""
    if not task_id:
        return
    if is_image_pipeline_done(task_id):
        return
    _start_bg(
        task_id,
        reason=reason or "step4_image_compare_completed",
        skip_analyze=False,
        force_analyze=True,
    )


def ensure_images_before_report(
    task_id: str,
    *,
    reason: str = "",
) -> Optional[Dict[str, Any]]:
    """成报前确保完整管线（下载+分析回填）；仅全部终态才秒回。"""
    if not task_id:
        return None
    if is_image_pipeline_done(task_id):
        return {"taskId": task_id, "skipped": True, "reason": "pipeline_done"}
    stats = _image_pipeline_stats(task_id)
    logger.info(
        "03 成报前图片入库 reason=%s task=%s total=%s open_storage=%s open_analyze=%s",
        reason or "-",
        task_id,
        stats["total"],
        stats["open_storage"],
        stats["open_analyze"],
    )
    return run_image_pipeline_for_verify(
        task_id,
        force_analyze=True,
        skip_if_stored=False,
        skip_analyze=False,
    )
