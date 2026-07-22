"""复用仓库 scripts/collect_01 的 MySQL 连接。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _ensure_scripts_path() -> None:
    root = Path(__file__).resolve().parent.parent.parent.parent
    scripts = root / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))


_ensure_scripts_path()

from collect_01 import db  # noqa: E402


def execute(sql: str, params: Optional[Tuple | Dict] = None) -> int:
    return db.execute(sql, params)


def fetch_one(sql: str, params: Optional[Tuple | Dict] = None) -> Optional[Dict[str, Any]]:
    return db.fetch_one(sql, params)


def fetch_all(sql: str, params: Optional[Tuple | Dict] = None) -> List[Dict[str, Any]]:
    return db.fetch_all(sql, params)


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
