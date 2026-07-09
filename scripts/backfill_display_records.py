"""从现有业务表按步骤全量回填 collect_display_records。"""
from __future__ import annotations

import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from collect_01 import db
from collect_01.display_store import backfill_task_displays


def backfill_all(limit: int = 500) -> None:
    tasks = db.fetch_all(
        "SELECT task_id FROM hermes_tasks ORDER BY updated_at DESC LIMIT %s",
        (limit,),
    )
    total = 0
    for t in tasks:
        tid = t["task_id"]
        c = backfill_task_displays(tid)
        total += c
        print(f"task {tid}: {c} display rows synced")
    print(f"done, total display sync ops: {total}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        for tid in sys.argv[1:]:
            print(f"task {tid}: {backfill_task_displays(tid)} display rows synced")
    else:
        backfill_all()
