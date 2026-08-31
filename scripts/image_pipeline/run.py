"""
图片资产管线入口。

用法（在 scripts 目录下）:
  python -m image_pipeline.run --task-id <taskId>
  python -m image_pipeline.run --task-id <taskId> --discover-only
  python -m image_pipeline.run --task-id <taskId> --skip-analyze
  python -m image_pipeline.run --task-id <taskId> --retry-failed
  python -m image_pipeline.run --task-id <taskId> --force-analyze
  python -m image_pipeline.run --task-id <taskId> --source-type post_media
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Callable, Dict, List, Optional

from image_pipeline import analyzer, discover, downloader, hbase_store, mysql_store
from image_pipeline.config import load_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("image_pipeline")


def _should_process_storage(row: Dict[str, Any], retry_failed: bool) -> bool:
    status = row.get("storage_status")
    if status == "stored" and row.get("hbase_row_key"):
        return False
    # 历史失败默认跳过，需显式 --retry-failed 才重跑
    if status == "failed" and not retry_failed:
        return False
    return True


def _select_capped_work_rows(
    rows: List[Dict[str, Any]],
    max_images: Optional[int],
) -> List[Dict[str, Any]]:
    """限额：已 completed 计入配额；剩余名额优先待下载/待分析。"""
    if max_images is None or max_images <= 0:
        return rows
    completed = [r for r in rows if r.get("analyze_status") == "completed"]
    need = [r for r in rows if r.get("analyze_status") != "completed"]
    if len(completed) >= max_images:
        return completed[:max_images]
    remaining = max_images - len(completed)
    return completed + need[:remaining]


def process_task(
    task_id: str,
    discover_only: bool = False,
    skip_analyze: bool = False,
    retry_failed: bool = False,
    force_analyze: bool = False,
    source_type: Optional[str] = None,
    progress_callback: Optional[Callable[[], None]] = None,
    progress_every: int = 5,
    max_images: Optional[int] = None,
) -> Dict[str, Any]:
    load_env()
    candidates = discover.discover_images(task_id)
    if source_type:
        candidates = [c for c in candidates if c.get("source_type") == source_type]
    discovered_total = len(candidates)
    # 限额时仍写入索引，但仅 upsert 前 N 条，避免一次入库上百条待办
    upsert_candidates = candidates
    if max_images is not None and max_images > 0:
        upsert_candidates = candidates[:max_images]
    for item in upsert_candidates:
        mysql_store.upsert_image_pending(item)

    summary: Dict[str, Any] = {
        "taskId": task_id,
        "discovered": discovered_total,
        "cappedTo": max_images,
        "upserted": len(upsert_candidates),
        "sourceType": source_type,
        "stored": 0,
        "storageFailed": 0,
        "storageSkipped": 0,
        "failedSkipped": 0,
        "analyzed": 0,
        "analyzeSkipped": 0,
        "analyzeFailed": 0,
        "profileImages": sum(1 for c in candidates if c["source_type"] in {"profile_avatar", "profile_cover"}),
        "postImages": sum(1 for c in candidates if c["source_type"] == "post_media"),
    }
    if max_images is not None and max_images > 0 and discovered_total > max_images:
        logger.info(
            "图片限额 max=%s discovered=%s，仅下载/分析前 %s 张 task=%s source=%s",
            max_images,
            discovered_total,
            max_images,
            task_id,
            source_type or "*",
        )

    if discover_only:
        return summary

    # 按库中最新状态处理（含历史失败重试）；source_type 限定时不碰其它来源
    rows = mysql_store.list_images_for_task(task_id, source_type=source_type)
    rows = _select_capped_work_rows(rows, max_images)
    download_budget = max_images if (max_images is not None and max_images > 0) else None
    downloaded_this_run = 0
    for row in rows:
        image_id = row["image_id"]
        if not _should_process_storage(row, retry_failed=retry_failed):
            if row.get("storage_status") == "stored":
                summary["stored"] += 1
                summary["storageSkipped"] += 1
            elif row.get("storage_status") == "failed":
                summary["failedSkipped"] += 1
                logger.info(
                    "跳过失败图片 image_id=%s，请加 --retry-failed 重试；上次错误: %s",
                    image_id,
                    row.get("error_message") or "",
                )
            continue

        if download_budget is not None and downloaded_this_run >= download_budget:
            summary["storageSkipped"] += 1
            continue

        url = row.get("origin_url") or ""
        if not url:
            mysql_store.mark_storage_failed(image_id, "origin_url 为空")
            summary["storageFailed"] += 1
            continue

        try:
            mysql_store.mark_downloading(image_id)
            dl = downloader.download_image(url, platform=row.get("platform"))
            # 同任务同哈希：复用已有 RowKey，避免重复写
            existing_key = None
            for other in rows:
                if (
                    other.get("content_sha256") == dl["sha256"]
                    and other.get("storage_status") == "stored"
                    and other.get("hbase_row_key")
                ):
                    existing_key = other["hbase_row_key"]
                    break
            if existing_key:
                row_key = existing_key
            else:
                row_key = hbase_store.put_image(
                    task_id=task_id,
                    sha256=dl["sha256"],
                    content=dl["bytes"],
                    mime_type=dl["mime_type"],
                    origin_url=url,
                    file_size=dl["file_size"],
                )
            mysql_store.mark_stored(
                image_id=image_id,
                hbase_row_key=row_key,
                content_sha256=dl["sha256"],
                mime_type=dl["mime_type"],
                file_size=dl["file_size"],
            )
            summary["stored"] += 1
            downloaded_this_run += 1
            # 刷新内存中的行，便于后续哈希复用
            row["storage_status"] = "stored"
            row["hbase_row_key"] = row_key
            row["content_sha256"] = dl["sha256"]
        except Exception as exc:
            logger.warning("store failed image_id=%s err=%s", image_id, exc)
            mysql_store.mark_storage_failed(image_id, str(exc))
            summary["storageFailed"] += 1

    if skip_analyze:
        return summary

    analyze_stats = analyzer.analyze_task(
        task_id,
        force=force_analyze,
        source_type=source_type,
        progress_callback=progress_callback,
        progress_every=progress_every,
        max_images=max_images,
    )
    summary["analyzed"] = analyze_stats.get("completed", 0)
    summary["analyzeSkipped"] = analyze_stats.get("skipped", 0) + analyze_stats.get("unchanged", 0)
    summary["analyzeFailed"] = analyze_stats.get("failed", 0)
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="图片资产发现/下载/入库/分析")
    parser.add_argument("--task-id", required=True, help="任务 ID")
    parser.add_argument("--discover-only", action="store_true", help="仅发现并写索引，不下载")
    parser.add_argument("--skip-analyze", action="store_true", help="只入库不分析")
    parser.add_argument("--retry-failed", action="store_true", help="重试 storage_status=failed 的图片")
    parser.add_argument("--force-analyze", action="store_true", help="强制重新分析已完成图片")
    parser.add_argument(
        "--source-type",
        default=None,
        help="仅处理指定来源（如 post_media）；不指定则处理全任务",
    )
    parser.add_argument(
        "--max-images",
        type=int,
        default=None,
        help="最多下载/分析 N 张（步骤8发文配图默认 20）",
    )
    args = parser.parse_args(argv)
    source_type = (args.source_type or "").strip() or None
    max_images = args.max_images if args.max_images and args.max_images > 0 else None

    try:
        summary = process_task(
            task_id=args.task_id,
            discover_only=args.discover_only,
            skip_analyze=args.skip_analyze,
            retry_failed=args.retry_failed,
            force_analyze=args.force_analyze,
            source_type=source_type,
            max_images=max_images,
        )
    except Exception as exc:
        logger.exception("pipeline failed: %s", exc)
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
