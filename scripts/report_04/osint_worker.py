# -*- coding: utf-8 -*-
"""04 写报 4.3 社工库独立子进程：不阻塞 Hook session_end。

用法：
  python -m report_04.osint_worker --task-id <id>
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
            logging.FileHandler(log_dir / "osint_worker.log", encoding="utf-8")
        )
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [osint_worker] %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="report_04 社工库 worker")
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    task_id = str(args.task_id or "").strip()
    if not task_id:
        logging.error("缺少 --task-id")
        return 2

    from report_04.osint_es import close_osint_on_session_end, kickoff_osint_if_ready
    from report_04.task_store import TaskStore

    store = TaskStore()
    logging.info("osint_worker start task=%s", task_id)
    try:
        kickoff_osint_if_ready(store, task_id)
    except Exception as exc:
        logging.exception("osint_worker kickoff 失败 task=%s: %s", task_id, exc)
    try:
        close_osint_on_session_end(store, task_id)
    except Exception as exc:
        logging.warning("osint_worker close 失败 task=%s: %s", task_id, exc)
    logging.info("osint_worker done task=%s", task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
