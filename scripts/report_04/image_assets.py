"""04 写报：步骤7发文后的图片资产入库 + 分析回填（复用 image_pipeline）。

主路径：Skill 要求 Agent 执行
  python -m image_pipeline.run --task-id <taskId> --force-analyze
Hook 仅在 7→8 / finalize / session_end 兜底，禁止进 post_tool 热路径。
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Hook 兜底等待上限，避免拖垮 db_sink 120s
_HOOK_TIMEOUT_SEC = 90


def count_stored_images(task_id: str) -> int:
    from collect_01.image_assets import count_stored_images as _count

    return _count(task_id)


def has_stored_images(task_id: str) -> bool:
    return count_stored_images(task_id) > 0


def spawn_second_image_pipeline(
    task_id: str,
    *,
    timeout_sec: int = _HOOK_TIMEOUT_SEC,
    skip_if_stored: bool = True,
) -> bool:
    """发文后第二次图片管线：独立进程后台跑，超时由子进程自杀。

    不阻塞步骤 6/7。4.1.2 第一次核验请继续用 run_image_pipeline_for_report
    （stream_steps.start_step5_image_pipeline），不要走本函数。
    """
    if not task_id:
        return False
    sec = int(timeout_sec or _HOOK_TIMEOUT_SEC)
    if sec <= 0:
        sec = _HOOK_TIMEOUT_SEC
    args = ["--task-id", str(task_id), "--timeout-sec", str(sec)]
    if skip_if_stored:
        args.append("--skip-if-stored")
    try:
        from report_04.session_continue import spawn_detached_python_module

        ok = spawn_detached_python_module("report_04.post_image_job", args)
        if ok:
            logger.info(
                "已后台拉起第二次图片管线 task=%s timeout=%ss",
                task_id,
                sec,
            )
        return bool(ok)
    except Exception as exc:
        logger.warning("spawn 第二次图片管线失败 task=%s: %s", task_id, exc)
        return False


def run_image_pipeline_for_report(
    task_id: str,
    *,
    force_analyze: bool = True,
    skip_if_stored: bool = True,
    timeout_sec: Optional[int] = _HOOK_TIMEOUT_SEC,
) -> Optional[Dict[str, Any]]:
    """4.1.2 第一次图片核验使用的同步管线（stream_steps 后台线程内调用）。

    发文后第二次补图请用 spawn_second_image_pipeline，勿在 7→8 热路径同步调用本函数。
    """
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
            logger.warning("04 图片管线失败 task=%s: %s", task_id, exc)
            return None

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run)
            try:
                return fut.result(timeout=timeout_sec)
            except FuturesTimeout:
                logger.warning(
                    "04 图片管线等待超时(%ss)，放弃阻塞 task=%s（不 fail 任务）",
                    timeout_sec,
                    task_id,
                )
                return None
    except Exception as exc:
        logger.warning("04 图片管线异常 task=%s: %s", task_id, exc)
        return None
