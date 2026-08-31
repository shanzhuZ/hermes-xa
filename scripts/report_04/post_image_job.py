# -*- coding: utf-8 -*-
"""发文后第二次图片管线：CLI 手工补跑（与 Hook 共用 in-process 逻辑，无子进程窗口）。

Hook 主路径请走 report_04.image_assets.start_step8_post_media_pipeline，
不得在此重复 spawn cmd。

用法：
  python -m report_04.post_image_job --task-id <id> --timeout-sec 600 --skip-if-stored
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_REPO = _SCRIPTS.parent
if not (os.environ.get("HERMES_HOME") or "").strip():
    os.environ["HERMES_HOME"] = str(_REPO)

logger = logging.getLogger("report_04.post_image_job")


def _setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    log_dir = Path(os.environ.get("HERMES_HOME") or _REPO) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    handlers = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(
            logging.FileHandler(log_dir / "post_image_job.log", encoding="utf-8")
        )
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [post_image_job] %(message)s",
        handlers=handlers,
    )


def main(argv: list | None = None) -> int:
    _setup_logging()
    from report_04.image_assets import (
        _POST_IMAGE_TIMEOUT_SEC,
        run_post_media_pipeline_blocking,
    )

    parser = argparse.ArgumentParser(description="04 发文后第二次图片管线（CLI 补跑）")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--timeout-sec", type=int, default=_POST_IMAGE_TIMEOUT_SEC)
    parser.add_argument(
        "--skip-if-stored",
        action="store_true",
        help="发文配图已全部入库且已分析则跳过（仍有待下载/待分析会继续跑）",
    )
    args = parser.parse_args(argv)
    task_id = str(args.task_id or "").strip()
    if not task_id:
        logger.error("缺少 --task-id")
        return 2

    store = None
    try:
        from report_04.task_store import TaskStore

        store = TaskStore()
    except Exception as exc:
        logger.warning("无法创建 TaskStore，step8 进度不回写 task=%s: %s", task_id, exc)

    return run_post_media_pipeline_blocking(
        task_id,
        timeout_sec=int(args.timeout_sec or _POST_IMAGE_TIMEOUT_SEC),
        skip_if_stored=bool(args.skip_if_stored),
        store=store,
    )


if __name__ == "__main__":
    raise SystemExit(main())
