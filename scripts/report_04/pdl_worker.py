# -*- coding: utf-8 -*-
"""04 写报 4.7 PDL 独立子进程：不阻塞 Hook。

用法：
  python -m report_04.pdl_worker --task-id <id>
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
            logging.FileHandler(log_dir / "pdl_worker.log", encoding="utf-8")
        )
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [pdl_worker] %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="report_04 PDL worker")
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    task_id = str(args.task_id or "").strip()
    if not task_id:
        logging.error("缺少 --task-id")
        return 2

    from report_04.pdl_enrich import close_pdl_on_session_end, kickoff_pdl_if_ready
    from report_04.task_store import TaskStore

    store = TaskStore()
    logging.info("pdl_worker start task=%s", task_id)
    try:
        kickoff_pdl_if_ready(store, task_id)
    except Exception as exc:
        logging.exception("pdl_worker kickoff 失败 task=%s: %s", task_id, exc)
    try:
        close_pdl_on_session_end(store, task_id)
    except Exception as exc:
        logging.warning("pdl_worker close 失败 task=%s: %s", task_id, exc)
    logging.info("pdl_worker done task=%s", task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
