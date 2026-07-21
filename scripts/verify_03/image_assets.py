"""03 账号核查：步骤3发文采集完成后的图片资产入库（复用 image_pipeline）。

主路径：Skill 要求 Agent 执行
  python -m image_pipeline.run --task-id <taskId> --force-analyze
Hook 仅在 finalize 兜底，禁止进 post_tool 热路径。
"""

from __future__ import annotations

import logging
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
