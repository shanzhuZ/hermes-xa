"""MySQL 连接与通用写入。"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple

from collect_01.config import db_config, persist_enabled

logger = logging.getLogger(__name__)


class DbError(RuntimeError):
    pass


def _connect():
    if not persist_enabled():
        raise DbError("HERMES_PERSIST_ENABLED=0，跳过入库")
    try:
        import pymysql
    except ImportError as exc:
        raise DbError(
            "未安装 pymysql，请执行: pip install -r scripts/requirements-persist.txt"
        ) from exc
    cfg = db_config()
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset=cfg["charset"],
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


@contextmanager
def transaction() -> Generator[Any, None, None]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(sql: str, params: Optional[Tuple | Dict] = None) -> int:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return int(cur.rowcount)


def insert_returning_id(sql: str, params: Optional[Tuple | Dict] = None) -> int:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return int(cur.lastrowid)


def fetch_one(sql: str, params: Optional[Tuple | Dict] = None) -> Optional[Dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            row = cur.fetchone()
            return dict(row) if row else None


def fetch_all(sql: str, params: Optional[Tuple | Dict] = None) -> List[Dict[str, Any]]:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return [dict(r) for r in cur.fetchall()]


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def upsert_many(table: str, rows: Iterable[Dict[str, Any]], unique_keys: List[str]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ", ".join(f"%({c})s" for c in cols)
    col_list = ", ".join(f"`{c}`" for c in cols)
    updates = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in cols if c not in unique_keys)
    sql = (
        f"INSERT INTO `{table}` ({col_list}) VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )
    count = 0
    with transaction() as conn:
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(sql, row)
                count += 1
    return count
