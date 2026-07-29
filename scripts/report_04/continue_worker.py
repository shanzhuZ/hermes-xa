# -*- coding: utf-8 -*-
"""04 写报续跑独立子进程入口：脱离 Hook，避免 120s 杀进程导致 inflight 死锁。

用法：
  python -m report_04.continue_worker --task-id <id> --reason <r> --kind <k>
  python -m report_04.continue_worker --task-id <id> --after-max
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# 保证 scripts 在 path（直接 python 本文件时）
_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_REPO = _SCRIPTS.parent
if not (os.environ.get("HERMES_HOME") or "").strip():
    os.environ["HERMES_HOME"] = str(_REPO)


def _setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    log_dir = Path(os.environ.get("HERMES_HOME") or _REPO) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    handlers: list = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(
            logging.FileHandler(
                log_dir / "continue_worker.log", encoding="utf-8"
            )
        )
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [continue_worker] %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="report_04 续跑 worker")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument("--kind", default="")
    parser.add_argument(
        "--after-max",
        action="store_true",
        help="续跑达上限：系统发文兜底 / failed",
    )
    args = parser.parse_args(argv)
    task_id = str(args.task_id or "").strip()
    if not task_id:
        logging.error("缺少 --task-id")
        return 2

    from report_04.session_continue import (
        run_after_continue_max,
        run_continue_worker_job,
    )
    from report_04.task_store import TaskStore

    store = TaskStore()
    if args.after_max:
        logging.info("continue_worker after_max task=%s", task_id)
        run_after_continue_max(store, task_id)
        return 0

    logging.info(
        "continue_worker start task=%s reason=%s kind=%s",
        task_id,
        str(args.reason or "")[:80],
        args.kind,
    )
    run_continue_worker_job(
        store,
        task_id,
        reason=str(args.reason or ""),
        kind=str(args.kind or ""),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
