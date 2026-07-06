#!/usr/bin/env python3
"""独立 worker：供 FastMCP async 工具通过子进程调用，避免 Windows stdio 下 maigret 卡死。"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_params() -> dict:
    """参数来源：argv[1] 为 JSON 文件路径（推荐）；否则读 stdin。"""
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        p = Path(sys.argv[1])
        return json.loads(p.read_text(encoding="utf-8"))
    raw = sys.stdin.read()
    return json.loads(raw or "{}")


def _emit(payload: dict, *, exit_code: int = 0) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    raise SystemExit(exit_code)


def main() -> None:
    if sys.platform == "win32":
        import os
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        params = _load_params()
        from server import _collect_accounts_impl  # noqa: E402

        result = _collect_accounts_impl(**params)
        _emit(result, exit_code=0)
    except SystemExit:
        raise
    except Exception as exc:
        _emit(
            {
                "error": str(exc),
                "timed_out": False,
                "scan_status": "failed",
                "user_message": "Maigret 扫描失败，继续种子账号流校验。",
                "summary": {"found_count": 0, "accounts": []},
                "traceback": traceback.format_exc()[-1500:],
            },
            exit_code=0,
        )


if __name__ == "__main__":
    main()
