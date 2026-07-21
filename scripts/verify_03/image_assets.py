"""03 账号核查：图片资产入库（复用 image_pipeline）。

时序硬约束：必须在三节终稿（summary）落库之前完成入库。
- 热路径（post_tool / step3 收口）：只后台 kickoff，禁止同步等待（否则 Hook 120s 超时）
- 闸门：save_assistant_output 写 summary 前 ensure（已有 stored 则秒回）
- finalize：仅当尚无 summary 时兜底（禁止「报告后才入库」）
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
    return count_stored_images(task_id) > 0


def run_image_pipeline_for_verify(
    task_id: str,
    *,
    force_analyze: bool = True,
    skip_if_stored: bool = True,
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


def kickoff_images_background(task_id: str, *, reason: str = "") -> None:
    """post_tool 热路径专用：后台启动，立即返回，绝不阻塞 Hook。"""
    if not task_id:
        return
    if has_stored_images(task_id):
        return

    def _bg() -> None:
        try:
            logger.info("03 图片管线后台启动 reason=%s task=%s", reason or "-", task_id)
            run_image_pipeline_for_verify(
                task_id,
                force_analyze=True,
                skip_if_stored=True,
                timeout_sec=None,
            )
        except Exception as exc:
            logger.warning("03 图片管线后台失败 reason=%s task=%s: %s", reason or "-", task_id, exc)

    threading.Thread(
        target=_bg,
        name=f"verify03-img-{task_id[:8]}",
        daemon=True,
    ).start()


def ensure_images_before_report(
    task_id: str,
    *,
    reason: str = "",
) -> Optional[Dict[str, Any]]:
    """成报前确保已尝试图片入库（已有 stored 则秒回）。仅用于写 summary / finalize。"""
    if not task_id:
        return None
    if has_stored_images(task_id):
        return {"taskId": task_id, "skipped": True, "reason": "already_stored"}
    logger.info("03 成报前图片入库 reason=%s task=%s", reason or "-", task_id)
    return run_image_pipeline_for_verify(
        task_id,
        force_analyze=True,
        skip_if_stored=True,
    )
