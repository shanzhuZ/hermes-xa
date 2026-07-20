# -*- coding: utf-8 -*-
"""自定义任务动态流程图 CLI。

示例（在仓库 scripts/ 目录，或 PYTHONPATH 含 scripts）：

  python -m custom_05.flow_cli upsert --task-id <id> --mode merge --steps-json '[...]'
  python -m custom_05.flow_cli begin  --task-id <id> --step-key research --message 开始检索
  python -m custom_05.flow_cli finish --task-id <id> --step-key research --status completed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from collect_01.config import load_env  # noqa: E402
from custom_05 import flow_store  # noqa: E402


def _print(obj) -> int:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


def main(argv=None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description="custom_05 动态流程图")
    sub = parser.add_subparsers(dest="cmd")


    p_up = sub.add_parser("upsert", help="批量写入/更新步骤")
    p_up.add_argument("--task-id", required=True)
    p_up.add_argument("--mode", default="merge", choices=["merge", "replace"])
    p_up.add_argument("--steps-json", required=True, help="JSON 数组字符串或 @文件路径")

    p_begin = sub.add_parser("begin", help="步骤开始 running")
    p_begin.add_argument("--task-id", required=True)
    p_begin.add_argument("--step-key", required=True)
    p_begin.add_argument("--message", default="")

    p_fin = sub.add_parser("finish", help="步骤结束")
    p_fin.add_argument("--task-id", required=True)
    p_fin.add_argument("--step-key", required=True)
    p_fin.add_argument("--status", default="completed", choices=["completed", "failed", "skipped"])
    p_fin.add_argument("--message", default="")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 2
    try:
        if args.cmd == "upsert":
            raw = args.steps_json.strip()
            if raw.startswith("@"):
                raw = Path(raw[1:]).read_text(encoding="utf-8")
            steps = json.loads(raw)
            if not isinstance(steps, list):
                raise ValueError("steps_json 须为数组")
            return _print(flow_store.upsert_steps(args.task_id, steps, mode=args.mode))
        if args.cmd == "begin":
            return _print(flow_store.begin_step(args.task_id, args.step_key, args.message or None))
        if args.cmd == "finish":
            return _print(
                flow_store.finish_step(
                    args.task_id, args.step_key, args.status, args.message or None
                )
            )
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
