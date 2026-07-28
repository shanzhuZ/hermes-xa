"""系统进度文案（已停用推送）。

历史上会 POST Java `/thoughts/progress` → 前端 SSE / hermes_thought_events。
该路径不进入 Hermes session messages，本身不影响模型上下文；
但甲方前端已不渲染 stream，且续跑文案易造成「双重催促」观感，故推送改为 no-op，仅打日志。
真正进模型上下文的是 Gateway `chat/stream` 的续跑 input（见 session_continue）。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 前端不渲染思考流：禁止 HTTP/DB 推送
_PUSH_ENABLED = False


def emit_system_thinking(task_id: str, content: str) -> bool:
    """兼容旧调用点。默认不推送；返回 False 表示未推送。"""
    text = (content or "").strip()
    if not task_id or not text:
        return False
    if len(text) > 2000:
        text = text[:2000]
    if not _PUSH_ENABLED:
        logger.debug(
            "emit_system_thinking 已禁用推送 task=%s content=%s",
            task_id,
            text[:80],
        )
        return False
    ok = _emit_via_http(task_id, text)
    if ok:
        return True
    return _emit_via_db(task_id, text)


def _api_base() -> str:
    import os

    return (
        os.environ.get("HERMES_XA_API_BASE")
        or os.environ.get("HERMES_COLLECT_API_BASE")
        or "http://127.0.0.1:4377"
    ).rstrip("/")


def _emit_via_http(task_id: str, content: str) -> bool:
    try:
        import requests
    except ImportError:
        return False
    url = f"{_api_base()}/api/tasks/{task_id}/thoughts/progress"
    try:
        resp = requests.post(url, json={"content": content}, timeout=3)
        if resp.status_code >= 200 and resp.status_code < 300:
            return True
        logger.info(
            "思考流 HTTP 推送非 2xx task=%s status=%s body=%s",
            task_id,
            resp.status_code,
            (resp.text or "")[:120],
        )
        return False
    except Exception as exc:
        logger.info("思考流 HTTP 推送失败（将回落 DB） task=%s: %s", task_id, exc)
        return False


def _emit_via_db(task_id: str, content: str) -> bool:
    try:
        from collect_01 import db

        row = db.fetch_one(
            "SELECT COALESCE(MAX(seq), 0) AS m FROM hermes_thought_events WHERE task_id=%s",
            (task_id,),
        )
        seq = int((row or {}).get("m") or 0) + 1
        db.execute(
            """
            INSERT IGNORE INTO hermes_thought_events
              (task_id, seq, event_type, tool_name, content)
            VALUES (%s, %s, 'tool.progress', '_thinking', %s)
            """,
            (task_id, seq, content[:2000]),
        )
        return True
    except Exception as exc:
        logger.warning("思考流 DB 回落失败 task=%s: %s", task_id, exc)
        return False
