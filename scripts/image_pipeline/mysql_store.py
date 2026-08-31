"""collect_images 及关联表的 MySQL 读写。"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Any, Dict, Generator, List, Optional, Tuple

from image_pipeline.config import db_config, persist_enabled

logger = logging.getLogger(__name__)


class DbError(RuntimeError):
    pass


def _connect():
    if not persist_enabled():
        raise DbError("HERMES_PERSIST_ENABLED=0，跳过入库")
    try:
        import pymysql
    except ImportError as exc:
        raise DbError("未安装 pymysql，请执行: pip install pymysql") from exc
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


def execute(sql: str, params: Optional[Tuple | Dict] = None) -> int:
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return int(cur.rowcount)


def json_dumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def fetch_task(task_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one(
        "SELECT task_id, task_type, status FROM hermes_tasks WHERE task_id=%s",
        (task_id,),
    )


def fetch_profiles(task_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        """
        SELECT platform, account_id, avatar_url, raw_json, extra_json
        FROM collect_profiles
        WHERE task_id=%s
        """,
        (task_id,),
    )


def fetch_posts(task_id: str) -> List[Dict[str, Any]]:
    return fetch_all(
        """
        SELECT platform, account_id, content_id, media_json, raw_json
        FROM collect_posts
        WHERE task_id=%s
        """,
        (task_id,),
    )


def upsert_image_pending(item: Dict[str, Any]) -> None:
    """发现阶段写入/更新索引行，不覆盖已 stored 的存储字段。"""
    sql = """
    INSERT INTO collect_images (
        image_id, task_id, task_type, source_type, platform,
        account_id, post_id, profile_id, origin_url,
        storage_status, analyze_status
    ) VALUES (
        %(image_id)s, %(task_id)s, %(task_type)s, %(source_type)s, %(platform)s,
        %(account_id)s, %(post_id)s, %(profile_id)s, %(origin_url)s,
        'pending', 'pending'
    )
    ON DUPLICATE KEY UPDATE
        task_type=VALUES(task_type),
        origin_url=VALUES(origin_url),
        account_id=VALUES(account_id),
        post_id=VALUES(post_id),
        profile_id=VALUES(profile_id),
        updated_at=CURRENT_TIMESTAMP(3)
    """
    execute(sql, item)


def mark_downloading(image_id: str) -> None:
    execute(
        """
        UPDATE collect_images
        SET storage_status='downloading', error_message=NULL, updated_at=CURRENT_TIMESTAMP(3)
        WHERE image_id=%s
        """,
        (image_id,),
    )


def mark_stored(
    image_id: str,
    hbase_row_key: str,
    content_sha256: str,
    mime_type: str,
    file_size: int,
) -> None:
    execute(
        """
        UPDATE collect_images
        SET storage_status='stored',
            hbase_row_key=%s,
            content_sha256=%s,
            mime_type=%s,
            file_size=%s,
            stored_at=CURRENT_TIMESTAMP(3),
            error_message=NULL,
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE image_id=%s
        """,
        (hbase_row_key, content_sha256, mime_type, file_size, image_id),
    )


def mark_storage_failed(image_id: str, error_message: str) -> None:
    msg = (error_message or "")[:1000]
    execute(
        """
        UPDATE collect_images
        SET storage_status='failed',
            error_message=%s,
            retry_count=retry_count+1,
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE image_id=%s
        """,
        (msg, image_id),
    )


def mark_analyze_running(image_id: str) -> None:
    execute(
        """
        UPDATE collect_images
        SET analyze_status='running', error_message=NULL, updated_at=CURRENT_TIMESTAMP(3)
        WHERE image_id=%s
        """,
        (image_id,),
    )


def mark_analyze_completed(
    image_id: str,
    ocr_text: Optional[str],
    vision_text: Optional[str],
    analysis_json: Any,
    tool_output_id: Optional[int] = None,
) -> None:
    execute(
        """
        UPDATE collect_images
        SET analyze_status='completed',
            ocr_text=%s,
            vision_text=%s,
            analysis_json=%s,
            tool_output_id=%s,
            analyzed_at=CURRENT_TIMESTAMP(3),
            error_message=NULL,
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE image_id=%s
        """,
        (ocr_text, vision_text, json_dumps(analysis_json), tool_output_id, image_id),
    )


def mark_analyze_failed(image_id: str, error_message: str) -> None:
    msg = (error_message or "")[:1000]
    execute(
        """
        UPDATE collect_images
        SET analyze_status='failed',
            error_message=%s,
            retry_count=retry_count+1,
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE image_id=%s
        """,
        (msg, image_id),
    )


def mark_analyze_skipped(image_id: str, reason: str) -> None:
    msg = (reason or "")[:1000]
    execute(
        """
        UPDATE collect_images
        SET analyze_status='skipped',
            error_message=%s,
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE image_id=%s
        """,
        (msg, image_id),
    )


def get_image(image_id: str) -> Optional[Dict[str, Any]]:
    return fetch_one("SELECT * FROM collect_images WHERE image_id=%s", (image_id,))


def list_images_for_task(
    task_id: str,
    storage_status: Optional[str] = None,
    analyze_status: Optional[str] = None,
    source_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM collect_images WHERE task_id=%s"
    params: List[Any] = [task_id]
    if storage_status:
        sql += " AND storage_status=%s"
        params.append(storage_status)
    if analyze_status:
        sql += " AND analyze_status=%s"
        params.append(analyze_status)
    if source_type:
        sql += " AND source_type=%s"
        params.append(source_type)
    sql += " ORDER BY created_at ASC"
    return fetch_all(sql, tuple(params))


def fetch_vision_tool_outputs(task_id: str) -> List[Dict[str, Any]]:
    """拉取任务内 OCR/Vision 工具输出，供 analyzer 回填。"""
    return fetch_all(
        """
        SELECT id, tool_name, tool_args, tool_output, status
        FROM hermes_tool_outputs
        WHERE task_id=%s
          AND tool_name IN (
            'mcp_ocr_perform_ocr',
            'mcp_vision_analyze',
            'vision_analyze',
            'mcp_ocr_perform_batch_ocr'
          )
        ORDER BY id ASC
        """,
        (task_id,),
    )
