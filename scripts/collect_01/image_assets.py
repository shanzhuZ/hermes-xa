"""01 采集：步骤6发文后的图片资产入库 + 分析回填（封装 image_pipeline）。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from collect_01 import db

logger = logging.getLogger(__name__)


def count_stored_images(task_id: str) -> int:
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_images
        WHERE task_id=%s AND storage_status='stored'
        """,
        (task_id,),
    )
    return int((row or {}).get("c") or 0)


def run_image_pipeline_after_posts(
    task_id: str,
    *,
    force_analyze: bool = True,
    skip_if_stored: bool = False,
    skip_analyze: bool = False,
) -> Optional[Dict[str, Any]]:
    """对任务跑图片发现/下载/入库/分析回填。

    失败只打日志并返回 None，不抛出（不得拖垮 01 主任务）。
    skip_analyze=True：只发现+下载入库，不做 OCR/Vision 回填（03 步骤3后加速用）。
    """
    if not task_id:
        return None
    if skip_if_stored and count_stored_images(task_id) > 0:
        logger.info("图片资产已存在，跳过管线 task=%s", task_id)
        return {"taskId": task_id, "skipped": True, "reason": "already_stored"}
    try:
        from image_pipeline.run import process_task

        summary = process_task(
            task_id=task_id,
            force_analyze=force_analyze,
            skip_analyze=skip_analyze,
        )
        logger.info(
            "图片资产管线完成 task=%s discovered=%s stored=%s analyzed=%s failed=%s",
            task_id,
            summary.get("discovered"),
            summary.get("stored"),
            summary.get("analyzed"),
            summary.get("storageFailed"),
        )
        return summary
    except Exception as exc:
        logger.warning("图片资产管线失败（不影响主任务） task=%s: %s", task_id, exc)
        return None
