#!/usr/bin/env python3
"""Hermes 入库 Hook 统一入口。

按 task_type / 业务类分发；当前仅实现 01 账号采集（account_collect）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
# Hook 子进程未必继承 HERMES_HOME，确保读仓库内 .env / config
if not os.environ.get("HERMES_HOME", "").strip():
    os.environ["HERMES_HOME"] = str(_REPO_ROOT)

from collect_01.sink import main

if __name__ == "__main__":
    raise SystemExit(main())
