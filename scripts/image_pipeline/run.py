"""
图片资产管线入口。

用法（在 scripts 目录下）:
  python -m image_pipeline.run --task-id <taskId>
  python -m image_pipeline.run --task-id <taskId> --discover-only
  python -m image_pipeline.run --task-id <taskId> --skip-analyze
  python -m image_pipeline.run --task-id <taskId> --retry-failed
  python -m image_pipeline.run --task-id <taskId> --force-analyze
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Dict, List, Optional

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


def process_task(
    task_id: str,
    discover_only: bool = False,
    skip_analyze: bool = False,
    retry_failed: bool = False,
    force_analyze: bool = False,
) -> Dict[str, Any]:
    load_env()
    candidates = discover.discover_images(task_id)
    for item in candidates:
        mysql_store.upsert_image_pending(item)

    summary: Dict[str, Any] = {
        "taskId": task_id,
        "discovered": len(candidates),
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

    if discover_only:
        return summary

    # 按库中最新状态处理（含历史失败重试）
    rows = mysql_store.list_images_for_task(task_id)
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

    analyze_stats = analyzer.analyze_task(task_id, force=force_analyze)
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
    args = parser.parse_args(argv)

    try:
        summary = process_task(
            task_id=args.task_id,
            discover_only=args.discover_only,
            skip_analyze=args.skip_analyze,
            retry_failed=args.retry_failed,
            force_analyze=args.force_analyze,
        )
    except Exception as exc:
        logger.exception("pipeline failed: %s", exc)
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
