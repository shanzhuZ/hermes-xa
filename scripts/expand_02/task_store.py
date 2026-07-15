"""02 账号扩建任务与步骤状态写入。"""

from __future__ import annotations

import json
import logging
import hashlib
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from collect_01 import db
from expand_02.phases import (
    PHASE_COLLECT,
    PHASE_ACCOUNT_FINALIZE,
    PHASE_CROSS_PLATFORM,
    PHASE_DONE,
    PHASE_RESOLVE_SEED,
    TASK_TYPE,
    PLATFORM_LABELS,
    POST_PARENT_STEP_KEY,
    initial_steps,
    post_step_key,
    post_step_node,
    post_step_order,
    post_step_title,
    root_steps_for_platform,
    step_phase,
)

logger = logging.getLogger(__name__)

_EXPAND_INTENT = re.compile(
    r"(account-expansion|account-intelligence-expand|账号扩建|扩建.*?(推特|twitter|微博|weibo|@))",
    re.I,
)
_CROSS_YES = re.compile(r"(跨平台|cross.?platform)", re.I)
_CROSS_NO = re.compile(r"(仅当前平台|只采当前平台|只要当前平台|不跨平台|不要跨平台|不需要跨平台|单平台)", re.I)
from collect_01.seed_platforms import parse_platform_from_message


def _now_sql() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _parse_seed(user_message: str) -> Dict[str, Any]:
    platform = parse_platform_from_message(user_message)
    handle = None
    hm = re.search(r"@([A-Za-z0-9_\.]+)", user_message or "")
    if hm:
        handle = hm.group(1)
    cross = 1
    if _CROSS_NO.search(user_message or ""):
        cross = 0
    elif _CROSS_YES.search(user_message or ""):
        cross = 1
    return {
        "platform": platform,
        "account_hint": handle or user_message.strip()[:128],
        "account_handle": handle,
    }


def is_expand_intent(user_message: str) -> bool:
    return bool(_EXPAND_INTENT.search(user_message or ""))


def is_four_section_report(content: str) -> bool:
    """判断是否为扩建四节终稿。"""
    text = (content or "").replace(" ", "").replace("\u3000", "")
    if len(text) < 30:
        return False
    markers = ("一、账号扩建收集", "二、多平台信息采集", "三、账号核查", "四、账号")
    return sum(1 for m in markers if m in text) >= 2


def _stream_id_base(task_id: str, platform: str, account_id: str) -> str:
    """stream_id 列 VARCHAR(64)，超长 account_id 用摘要。"""
    digest = hashlib.md5(f"{task_id}:{platform}:{account_id}".encode("utf-8")).hexdigest()[:16]
    return f"{task_id[:8]}:{platform[:8]}:{digest}"


def _extract_image_source(tool_args: Dict[str, Any]) -> str:
    from collect_01.image_stream_match import extract_image_source

    return extract_image_source(tool_args)


def _find_image_stream_for_tool(task_id: str, tool_args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from collect_01.image_stream_match import find_image_stream_for_tool

    return find_image_stream_for_tool(task_id, tool_args)


def _step_status(task_id: str, step_key: str) -> str:
    row = db.fetch_one(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, step_key),
    )
    return str((row or {}).get("status") or "").strip() or "pending"


def _expand_post_step_rows(task_id: str) -> List[Dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
          AND step_key LIKE %s AND step_key <> 'step6_posts'
        ORDER BY step_order, step_key
        """,
        (task_id, POST_PARENT_STEP_KEY, "step6_post_%"),
    )


def _reconcile_expand_post_child_steps(store: "TaskStore", task_id: str) -> int:
    """02 扩建：发文子步骤挂在 step3_profiles 下，不再单独维护 step6_posts 根节点。"""
    # 先收口「工具已失败但仍 running」以及「有主页/工具但仍 pending」的平台
    try:
        from expand_02.sink import (
            _finalize_failed_platform_attempts,
            _finalize_stale_post_children,
            _mark_step3_closed,
        )

        _finalize_failed_platform_attempts(store, task_id)
        _finalize_stale_post_children(store, task_id)
    except Exception:
        pass

    post_counts = {
        str(r["platform"]): int(r["c"])
        for r in db.fetch_all(
            "SELECT platform, COUNT(*) AS c FROM collect_posts WHERE task_id=%s GROUP BY platform",
            (task_id,),
        )
    }
    validated = {
        str(r["platform"])
        for r in db.fetch_all(
            """
            SELECT DISTINCT platform FROM collect_validated_accounts
            WHERE task_id=%s AND verdict='validated'
            """,
            (task_id,),
        )
    }
    expected = validated | set(post_counts.keys())
    if expected:
        store.ensure_post_steps(task_id, sorted(expected))
    updated = 0
    for row in _expand_post_step_rows(task_id):
        step_key = str(row.get("step_key") or "")
        if not step_key.startswith("step6_post_"):
            continue
        platform = step_key.replace("step6_post_", "", 1)
        cur = str(row.get("status") or "")
        if cur in {"completed", "skipped", "failed"}:
            continue
        cnt = post_counts.get(platform, 0)
        if cnt > 0:
            store.set_step_status(
                task_id,
                step_key,
                "completed",
                message=f"已采集 {platform} 发文 {cnt} 条",
                payload={"post_count": cnt},
            )
            updated += 1
            continue
        if platform in validated:
            store.set_step_status(
                task_id,
                step_key,
                "skipped",
                message=f"{platform} 已纳入可信账号，但未采集到发文",
            )
            updated += 1
    try:
        from expand_02.sink import _mark_step3_closed

        _mark_step3_closed(store, task_id)
    except Exception:
        pass
    return updated


class TaskStore:
    """02 账号扩建入库门面。"""

    def get_task_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        if not session_id:
            return None
        return db.fetch_one(
            """
            SELECT * FROM hermes_tasks
            WHERE session_id=%s AND task_type=%s
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id, TASK_TYPE),
        )

    def get_active_task_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """同一会话下 pending/running 的采集任务（Java 预插后 Hook 靠此对齐 task_id）。"""
        if not session_id:
            return None
        return db.fetch_one(
            """
            SELECT * FROM hermes_tasks
            WHERE session_id=%s AND task_type=%s AND status IN ('pending', 'running')
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id, TASK_TYPE),
        )

    def list_tasks_by_session(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        if not session_id:
            return []
        return db.fetch_all(
            """
            SELECT task_id, session_id, status, current_phase, cross_platform,
                   started_at, finished_at, created_at, updated_at
            FROM hermes_tasks
            WHERE session_id=%s AND task_type=%s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (session_id, TASK_TYPE, limit),
        )

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return db.fetch_one("SELECT * FROM hermes_tasks WHERE task_id=%s", (task_id,))

    def has_user_dialogue(self, task_id: str, msg_type: str = "user_input") -> bool:
        row = db.fetch_one(
            """
            SELECT id FROM hermes_user_dialogues
            WHERE task_id=%s AND msg_type=%s
            LIMIT 1
            """,
            (task_id, msg_type),
        )
        return bool(row)

    def bind_session(self, task_id: str, session_id: str) -> None:
        """Java 预建任务后绑定 Hermes session，并标记为 running。"""
        if not task_id or not session_id:
            return
        db.execute(
            """
            UPDATE hermes_tasks
            SET session_id=%s,
                status=CASE WHEN status='pending' THEN 'running' ELSE status END,
                started_at=COALESCE(started_at, NOW(3)),
                updated_at=NOW(3)
            WHERE task_id=%s
            """,
            (session_id, task_id),
        )

    def mark_task_failed(self, task_id: str, error_message: str) -> None:
        db.execute(
            """
            UPDATE hermes_tasks
            SET status='failed', error_message=%s, finished_at=NOW(3), updated_at=NOW(3)
            WHERE task_id=%s AND status NOT IN ('failed', 'completed')
            """,
            (error_message[:2000], task_id),
        )

    def fail_seed_and_abort(self, task_id: str, error_message: str) -> None:
        """种子主页采集失败：步骤一 failed、任务 failed，未完成步骤一律 skipped。"""
        msg = (error_message or "种子账号采集失败，请检查账号名").strip()
        cur1 = _step_status(task_id, "step1_seed")
        if cur1 not in {"completed", "failed", "skipped"}:
            self.set_step_status(task_id, "step1_seed", "failed", message=msg[:500])
        self.mark_task_failed(task_id, msg)
        db.execute(
            """
            UPDATE collect_phase_steps
            SET status='skipped',
                message=%s,
                updated_at=NOW(3),
                finished_at=COALESCE(finished_at, NOW(3))
            WHERE task_id=%s
              AND step_key <> 'step1_seed'
              AND status IN ('pending', 'running')
            """,
            ("种子账号采集失败，已中止后续步骤", task_id),
        )

    def create_pending_task(
        self,
        task_id: str,
        session_id: str,
        user_message: str,
        *,
        cross_platform: Optional[int] = None,
    ) -> str:
        """Java/Spring 在调 Gateway 前预建任务（status=pending），Hook 接手后转 running。"""
        if not task_id or not session_id:
            raise ValueError("task_id 与 session_id 不能为空")
        if not is_expand_intent(user_message):
            raise ValueError("非账号扩建意图消息，无法创建任务")
        existing = self.get_task(task_id)
        if existing:
            return task_id
        active = self.get_active_task_by_session(session_id)
        if active:
            raise ValueError(
                f"会话 {session_id} 已有进行中的任务 {active['task_id']}，请等待结束后再发起"
            )
        seed = _parse_seed(user_message)
        if cross_platform is None:
            cross_platform = 1
        initial_phase_steps = initial_steps(bool(cross_platform), seed.get("platform"))
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hermes_tasks
                      (task_id, task_type, session_id, status, current_phase, cross_platform, seed_json)
                    VALUES (%s, %s, %s, 'pending', %s, %s, %s)
                    """,
                    (
                        task_id,
                        TASK_TYPE,
                        session_id,
                        PHASE_RESOLVE_SEED,
                        cross_platform,
                        db.json_dumps(seed),
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO hermes_user_dialogues (task_id, session_id, role, content, msg_type)
                    VALUES (%s, %s, 'user', %s, 'user_input')
                    """,
                    (task_id, session_id, user_message[:65535]),
                )
                for step in initial_phase_steps:
                    cur.execute(
                        """
                        INSERT IGNORE INTO collect_phase_steps
                          (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                        VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (task_id, step.step_key, step.parent_step_key, step.step_order, step.step_node, step.title),
                    )
        logger.info("预建扩建任务 task_id=%s session=%s", task_id, session_id)
        return task_id

    def ensure_task(
        self,
        *,
        session_id: str,
        user_message: str,
        task_id: Optional[str] = None,
    ) -> Optional[str]:
        if not is_expand_intent(user_message):
            return None
        if task_id:
            row = self.get_task(task_id)
            if row:
                if session_id:
                    self.bind_session(task_id, session_id)
                return task_id
        existing = self.get_active_task_by_session(session_id) if session_id else None
        if existing:
            return existing["task_id"]

        new_id = task_id or str(uuid.uuid4())
        seed = _parse_seed(user_message)
        cross_platform = 1
        initial_phase_steps = initial_steps(bool(cross_platform), seed.get("platform"))
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hermes_tasks
                      (task_id, task_type, session_id, status, current_phase, cross_platform, seed_json, started_at)
                    VALUES (%s, %s, %s, 'running', %s, %s, %s, NOW(3))
                    """,
                    (
                        new_id,
                        TASK_TYPE,
                        session_id or None,
                        PHASE_RESOLVE_SEED,
                        cross_platform,
                        db.json_dumps(seed),
                    ),
                )
                cur.execute(
                    """
                    INSERT INTO hermes_user_dialogues (task_id, session_id, role, content, msg_type)
                    VALUES (%s, %s, 'user', %s, 'user_input')
                    """,
                    (new_id, session_id, user_message[:65535]),
                )
                for step in initial_phase_steps:
                    cur.execute(
                        """
                        INSERT IGNORE INTO collect_phase_steps
                          (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                        VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (new_id, step.step_key, step.parent_step_key, step.step_order, step.step_node, step.title),
                    )
        self.set_step_status(new_id, "step1_seed", "running", message="等待种子账号资料采集…")
        self.set_step_status(new_id, "step2_cross_platform", "running", message="等待 Maigret 跨平台发现…")
        logger.info("创建扩建任务 task_id=%s session=%s", new_id, session_id)
        return new_id

    def save_dialogue(
        self,
        task_id: str,
        session_id: Optional[str],
        role: str,
        content: str,
        msg_type: str = "assistant_reply",
    ) -> None:
        if not content or not task_id:
            return
        db.execute(
            """
            INSERT INTO hermes_user_dialogues (task_id, session_id, role, content, msg_type)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (task_id, session_id, role, content[:65535], msg_type),
        )

    def save_assistant_output(
        self,
        task_id: str,
        session_id: Optional[str],
        content: str,
    ) -> bool:
        """保存模型输出到 MySQL hermes_user_dialogues。"""
        text = (content or "").strip()
        if not text or not task_id or text == "(empty)":
            return False
        clipped = text[:65535]
        is_report = is_four_section_report(text)
        msg_type = "summary" if is_report else "assistant_reply"
        if is_report:
            existing = db.fetch_one(
                """
                SELECT id FROM hermes_user_dialogues
                WHERE task_id=%s AND msg_type='summary'
                LIMIT 1
                """,
                (task_id,),
            )
            if existing:
                db.execute(
                    """
                    UPDATE hermes_user_dialogues
                    SET session_id=%s, role='assistant', content=%s, created_at=NOW(3)
                    WHERE id=%s
                    """,
                    (session_id, clipped, existing["id"]),
                )
                logger.info("已更新扩建终稿 task=%s len=%d", task_id, len(text))
                return True
        dup = db.fetch_one(
            """
            SELECT id FROM hermes_user_dialogues
            WHERE task_id=%s AND role='assistant' AND msg_type=%s AND content=%s
            LIMIT 1
            """,
            (task_id, msg_type, clipped),
        )
        if dup:
            return False
        self.save_dialogue(task_id, session_id, "assistant", text, msg_type)
        logger.info("已保存助手输出 task=%s type=%s len=%d", task_id, msg_type, len(text))
        return True

    def _seed_platform(self, task_id: str) -> str:
        task = self.get_task(task_id) or {}
        try:
            seed = json.loads(task.get("seed_json") or "{}")
        except json.JSONDecodeError:
            seed = {}
        return str((seed or {}).get("platform") or "twitter")

    def init_phase_steps(self, task_id: str) -> None:
        for step in root_steps_for_platform(self._seed_platform(task_id)):
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                """,
                (task_id, step.step_key, step.parent_step_key, step.step_order, step.step_node, step.title),
            )

    def ensure_post_steps(self, task_id: str, platforms: List[str]) -> None:
        parent = POST_PARENT_STEP_KEY
        for platform in sorted(set(platforms), key=post_step_order):
            key = post_step_key(platform)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                """,
                (task_id, key, parent, post_step_order(platform), post_step_node(platform), post_step_title(platform)),
            )

    def set_task_phase(self, task_id: str, phase: str) -> None:
        db.execute(
            "UPDATE hermes_tasks SET current_phase=%s, updated_at=NOW(3) WHERE task_id=%s",
            (phase, task_id),
        )

    def set_step_status(
        self,
        task_id: str,
        step_key: str,
        status: str,
        *,
        message: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        progress_pct: Optional[int] = None,
    ) -> None:
        self.ensure_step_row(task_id, step_key)
        current = db.fetch_one(
            "SELECT status, finished_at FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
            (task_id, step_key),
        )
        cur_status = str((current or {}).get("status") or "")

        fields = ["status=%s", "updated_at=NOW(3)"]
        params: List[Any] = [status]
        if status == "running":
            fields.append("started_at=COALESCE(started_at, NOW(3))")
            if cur_status in {"completed", "failed", "skipped"}:
                fields.append("finished_at=NULL")
        elif status == "pending":
            fields.append("finished_at=NULL")
        elif status in {"completed", "failed", "skipped"}:
            # 终态时间只写一次，避免后续流程把 finished_at 往后推造成「假顺序」
            fields.append("finished_at=COALESCE(finished_at, NOW(3))")
        if message is not None:
            fields.append("message=%s")
            params.append(message[:2000])
        if payload is not None:
            fields.append("payload_json=%s")
            params.append(db.json_dumps(payload))
        if progress_pct is not None:
            fields.append("progress_pct=%s")
            params.append(progress_pct)
        params.extend([task_id, step_key])
        db.execute(
            f"UPDATE collect_phase_steps SET {', '.join(fields)} WHERE task_id=%s AND step_key=%s",
            tuple(params),
        )
        phase = step_phase(step_key)
        if phase:
            self.set_task_phase(task_id, phase)

    def ensure_step_row(self, task_id: str, step_key: str) -> None:
        """旧任务可能缺少 step3_profiles 等新步骤行。"""
        for step in root_steps_for_platform(self._seed_platform(task_id)):
            if step.step_key == step_key:
                db.execute(
                    """
                    INSERT IGNORE INTO collect_phase_steps
                      (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                    VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                    """,
                    (task_id, step.step_key, step.parent_step_key, step.step_order, step.step_node, step.title),
                )
                return
        if step_key.startswith("step6_post_"):
            platform = step_key.replace("step6_post_", "", 1)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                """,
                (task_id, step_key, POST_PARENT_STEP_KEY, post_step_order(platform), post_step_node(platform), post_step_title(platform)),
            )

    def save_tool_output(
        self,
        *,
        task_id: str,
        tool_name: str,
        tool_args: Optional[Dict[str, Any]],
        tool_output: str,
        tool_call_id: str,
        duration_ms: Optional[int],
        status: str,
        phase: Optional[str],
        mcp_server: Optional[str],
    ) -> int:
        return db.insert_returning_id(
            """
            INSERT INTO hermes_tool_outputs
              (task_id, phase, mcp_server, tool_name, tool_args, tool_output,
               tool_call_id, duration_ms, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              tool_output=VALUES(tool_output),
              duration_ms=VALUES(duration_ms),
              status=VALUES(status),
              phase=VALUES(phase),
              executed_at=NOW(3)
            """,
            (
                task_id,
                phase,
                mcp_server,
                tool_name,
                db.json_dumps(tool_args or {}),
                tool_output,
                tool_call_id,
                duration_ms,
                status,
            ),
        )

    def update_tool_output_phase(self, tool_output_id: int, step_key: str) -> None:
        """将工具调用记录的 phase 修正为 collect_phase_steps.step_key。"""
        if not step_key:
            return
        db.execute(
            "UPDATE hermes_tool_outputs SET phase=%s WHERE id=%s",
            (step_key, tool_output_id),
        )

    def save_profile_row(self, row: Dict[str, Any], *, step_key: str = "step3_profiles") -> None:
        db.execute(
            """
            INSERT INTO collect_profiles
              (task_id, platform, account_id, account_handle, display_name, bio, avatar_url,
               profile_url, follower_count, following_count, content_count, verified,
               visibility, collect_status, tool_output_id, raw_json)
            VALUES
              (%(task_id)s, %(platform)s, %(account_id)s, %(account_handle)s, %(display_name)s,
               %(bio)s, %(avatar_url)s, %(profile_url)s, %(follower_count)s, %(following_count)s,
               %(content_count)s, %(verified)s, %(visibility)s, %(collect_status)s,
               %(tool_output_id)s, %(raw_json)s)
            ON DUPLICATE KEY UPDATE
              display_name=VALUES(display_name), bio=VALUES(bio), avatar_url=VALUES(avatar_url),
              profile_url=VALUES(profile_url), follower_count=VALUES(follower_count),
              following_count=VALUES(following_count), content_count=VALUES(content_count),
              verified=VALUES(verified), collect_status=VALUES(collect_status),
              tool_output_id=VALUES(tool_output_id), raw_json=VALUES(raw_json),
              collected_at=NOW(3)
            """,
            row,
        )
        try:
            from collect_01.display_store import sync_profile_display

            sync_profile_display(row, step_key=step_key)
        except Exception as exc:
            logger.warning("展示层双写 profile 失败: %s", exc)

    def save_candidate_rows(self, rows: List[Dict[str, Any]], *, step_key: str = "step2_cross_platform") -> None:
        for row in rows:
            db.execute(
                """
                INSERT INTO cross_platform_candidates
                  (task_id, platform, account_id, account_handle, confidence, evidence_json,
                   match_strategy, status, raw_json, tool_output_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    row["task_id"],
                    row["platform"],
                    row["account_id"],
                    row.get("account_handle"),
                    row.get("confidence"),
                    db.json_dumps(row.get("evidence_json") or {}),
                    row.get("match_strategy", "maigret"),
                    row.get("status", "candidate"),
                    db.json_dumps(row.get("raw_json") or {}),
                    row.get("tool_output_id"),
                ),
            )
            try:
                from collect_01.display_store import sync_candidate_display

                sync_candidate_display(row["task_id"], row["platform"], row["account_id"], step_key=step_key)
            except Exception as exc:
                logger.warning("展示层双写 candidate 失败: %s", exc)

    def save_post_rows(self, rows: List[Dict[str, Any]], *, step_key: str = POST_PARENT_STEP_KEY) -> None:
        for row in rows:
            db.execute(
                """
                INSERT INTO collect_posts
                  (task_id, platform, account_id, content_id, content_type, parent_content_id,
                   title, content_text, content_url, published_at, view_count, like_count,
                   comment_count, repost_count, media_json, tool_output_id, raw_json)
                VALUES
                  (%(task_id)s,%(platform)s,%(account_id)s,%(content_id)s,%(content_type)s,
                   %(parent_content_id)s,%(title)s,%(content_text)s,%(content_url)s,%(published_at)s,
                   %(view_count)s,%(like_count)s,%(comment_count)s,%(repost_count)s,
                   %(media_json)s,%(tool_output_id)s,%(raw_json)s)
                ON DUPLICATE KEY UPDATE
                  content_text=VALUES(content_text), view_count=VALUES(view_count),
                  like_count=VALUES(like_count), comment_count=VALUES(comment_count),
                  repost_count=VALUES(repost_count), raw_json=VALUES(raw_json)
                """,
                row,
            )
            try:
                from collect_01.display_store import sync_post_display_for_tool

                sync_post_display_for_tool(row, step_key=step_key)
            except Exception as exc:
                logger.warning("展示层双写 post 失败: %s", exc)

    def build_streams_from_profile(self, task_id: str, profile: Dict[str, Any]) -> None:
        """步骤四：从 profile 拆文本流/图片流。"""
        platform = profile["platform"]
        account_id = profile["account_id"]
        stream_id_base = _stream_id_base(task_id, platform, account_id)
        text_parts = [
            ("display_name", profile.get("display_name")),
            ("account_handle", profile.get("account_handle")),
            ("bio", profile.get("bio")),
        ]
        for field, value in text_parts:
            if not value:
                continue
            sid = f"{stream_id_base}:text:{field}"
            db.execute(
                """
                INSERT INTO collect_identity_streams
                  (stream_id, task_id, stream_type, source_platform, source_account_id,
                   source_field, payload_text, validation_status)
                VALUES (%s,%s,'text',%s,%s,%s,%s,'pending')
                ON DUPLICATE KEY UPDATE payload_text=VALUES(payload_text), updated_at=NOW(3)
                """,
                (sid, task_id, platform, account_id, field, str(value)[:4000]),
            )
            try:
                from collect_01.display_store import sync_stream_display

                sync_stream_display(sid, step_key="step3_streams")
            except Exception as exc:
                logger.warning("展示层双写 stream 失败: %s", exc)
        avatar = profile.get("avatar_url")
        if avatar:
            sid = f"{stream_id_base}:image:avatar"
            db.execute(
                """
                INSERT INTO collect_identity_streams
                  (stream_id, task_id, stream_type, source_platform, source_account_id,
                   source_field, payload_url, validation_status)
                VALUES (%s,%s,'image',%s,%s,'avatar',%s,'pending')
                ON DUPLICATE KEY UPDATE payload_url=VALUES(payload_url), updated_at=NOW(3)
                """,
                (sid, task_id, platform, account_id, avatar),
            )
            try:
                from collect_01.display_store import sync_stream_display

                sync_stream_display(sid, step_key="step3_streams")
            except Exception as exc:
                logger.warning("展示层双写 stream 失败: %s", exc)
        # 仅写 collect_identity_streams，不更新步骤状态（避免 step1 期间误亮「步骤四：流拆分」）

    def mark_image_stream_progress(
        self,
        task_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        *,
        success: bool,
    ) -> bool:
        """OCR/Vision 调用后更新对应图片流；vision 成功/失败才视为该流处理完毕。"""
        if tool_name not in {"mcp_ocr_perform_ocr", "mcp_vision_analyze", "vision_analyze"}:
            return False
        stream = _find_image_stream_for_tool(task_id, tool_args)
        if not stream:
            return False
        stream_id = str(stream.get("stream_id") or "")
        if not stream_id:
            return False
        if tool_name == "mcp_ocr_perform_ocr":
            if success:
                db.execute(
                    """
                    UPDATE collect_identity_streams
                    SET validation_detail=%s, updated_at=NOW(3)
                    WHERE stream_id=%s AND validation_status='pending'
                    """,
                    ("OCR 完成，等待 vision", stream_id),
                )
            return True
        status = "processed" if success else "fail"
        detail = "vision 分析完成" if success else "vision 分析失败"
        if not success:
            source = _extract_image_source(tool_args)
            if source.startswith(("http://", "https://")):
                # 远程 URL 失败通常还会本地下载重试，暂不标记终态
                db.execute(
                    """
                    UPDATE collect_identity_streams
                    SET validation_detail=%s, updated_at=NOW(3)
                    WHERE stream_id=%s AND validation_status='pending'
                    """,
                    ("vision 远程 URL 失败，等待本地重试", stream_id),
                )
                return True
        db.execute(
            """
            UPDATE collect_identity_streams
            SET validation_status=%s, validation_detail=%s, updated_at=NOW(3)
            WHERE stream_id=%s
            """,
            (status, detail, stream_id),
        )
        try:
            from collect_01.display_store import sync_stream_display

            sync_stream_display(stream_id, step_key="step4_image_compare")
        except Exception as exc:
            logger.warning("展示层双写 stream 失败: %s", exc)
        return True

    def mark_remaining_image_streams_failed(self, task_id: str, detail: str) -> int:
        """图片流仍未处理时兜底标记失败，避免步骤四永久卡住。"""
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE collect_identity_streams
                    SET validation_status='fail', validation_detail=%s, updated_at=NOW(3)
                    WHERE task_id=%s AND stream_type='image' AND validation_status='pending'
                    """,
                    (detail[:500], task_id),
                )
                n = int(cur.rowcount or 0)
        try:
            from collect_01.display_store import sync_streams_for_task

            sync_streams_for_task(task_id, step_key="step4_image_compare", stream_type="image")
        except Exception as exc:
            logger.warning("展示层双写 image streams 失败: %s", exc)
        return n

    def run_text_compare(self, task_id: str) -> None:
        """步骤四：文本流与种子比对（规则，不依赖模型）。"""
        task = self.get_task(task_id)
        if not task:
            return
        if _step_status(task_id, "step4_text_compare") != "completed":
            self.set_step_status(task_id, "step4_text_compare", "running", message="文本流规则比对中")
        seed = {}
        try:
            seed = json.loads(task.get("seed_json") or "{}")
        except json.JSONDecodeError:
            pass
        seed_platform = seed.get("platform", "twitter")
        seed_handle = (seed.get("account_handle") or seed.get("account_hint") or "").lower().strip().lstrip("@")
        seed_rows = db.fetch_all(
            """
            SELECT * FROM collect_identity_streams
            WHERE task_id=%s AND stream_type='text'
              AND source_platform=%s
            """,
            (task_id, seed_platform),
        )
        seed_text = " ".join(
            (r.get("payload_text") or "").lower() for r in seed_rows
        ).strip()
        if not seed_text:
            self.set_step_status(
                task_id,
                "step4_text_compare",
                "pending",
                message=f"种子平台 {seed_platform} 无文本流，跳过自动比对（需先入库种子 profile）",
            )
            return
        all_text = db.fetch_all(
            "SELECT * FROM collect_identity_streams WHERE task_id=%s AND stream_type='text'",
            (task_id,),
        )
        matched = 0
        for row in all_text:
            platform = row.get("source_platform") or ""
            field = row.get("source_field") or ""
            text = (row.get("payload_text") or "").lower().strip()
            if not text:
                continue
            if platform == seed_platform:
                status = "pass"
            elif field == "account_handle":
                # handle 仅允许精确匹配，禁止子串误伤（如 tiktok 空号同名 handle）
                status = "pass" if seed_handle and text.lstrip("@") == seed_handle else "fail"
            elif field in ("display_name", "bio"):
                seed_vals = [
                    (r.get("payload_text") or "").lower().strip()
                    for r in seed_rows
                    if r.get("source_field") == field
                ]
                status = "fail"
                for sv in seed_vals:
                    if not sv or len(text) < 2:
                        continue
                    if text == sv:
                        status = "pass"
                        break
                    if len(text) >= 4 and len(sv) >= 4 and (text in sv or sv in text):
                        status = "pass"
                        break
            else:
                status = "fail"
            if status == "pass":
                matched += 1
            db.execute(
                "UPDATE collect_identity_streams SET validation_status=%s, validation_detail=%s WHERE stream_id=%s",
                (status, "文本流规则比对", row["stream_id"]),
            )
        try:
            from collect_01.display_store import sync_streams_for_task

            sync_streams_for_task(task_id, step_key="step4_text_compare", stream_type="text")
        except Exception as exc:
            logger.warning("展示层双写 text streams 失败: %s", exc)
        self.set_step_status(
            task_id,
            "step4_text_compare",
            "completed",
            message=f"文本流比对完成，通过 {matched}/{len(all_text)} 条",
            payload={"matched": matched, "total": len(all_text)},
        )

    def run_validated_accounts(self, task_id: str) -> None:
        """步骤五：根据文本流 pass + 有 profile 的账号写入可信清单。"""
        profiles = db.fetch_all(
            "SELECT platform, account_id, account_handle FROM collect_profiles WHERE task_id=%s",
            (task_id,),
        )
        task = self.get_task(task_id) or {}
        seed = {}
        try:
            seed = json.loads(task.get("seed_json") or "{}")
        except json.JSONDecodeError:
            pass
        seed_platform = seed.get("platform", "twitter")
        platforms_for_posts: List[str] = []
        count = 0
        for p in profiles:
            platform = p["platform"]
            account_id = p["account_id"]
            is_seed = int(platform == seed_platform)
            streams = db.fetch_all(
                """
                SELECT stream_id, validation_status, source_field FROM collect_identity_streams
                WHERE task_id=%s AND source_platform=%s AND source_account_id=%s
                """,
                (task_id, platform, account_id),
            )
            passes = [s for s in streams if s.get("validation_status") == "pass"]
            substantive = [
                s for s in passes if s.get("source_field") in ("display_name", "bio")
            ]
            if is_seed:
                verdict = "validated"
            elif substantive:
                verdict = "validated"
            else:
                verdict = "insufficient"
            if verdict == "validated":
                platforms_for_posts.append(platform)
                count += 1
            db.execute(
                """
                INSERT INTO collect_validated_accounts
                  (task_id, platform, account_id, account_handle, verdict, is_seed, stream_ids_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE verdict=VALUES(verdict), stream_ids_json=VALUES(stream_ids_json)
                """,
                (
                    task_id,
                    platform,
                    account_id,
                    p.get("account_handle"),
                    verdict,
                    is_seed,
                    db.json_dumps([s["stream_id"] for s in passes]),
                ),
            )
            try:
                from collect_01.display_store import sync_validated_display

                sync_validated_display(task_id, platform, account_id, step_key="step5_validated")
            except Exception as exc:
                logger.warning("展示层双写 validated 失败: %s", exc)
        if platforms_for_posts:
            self.ensure_post_steps(task_id, platforms_for_posts)
        keep_keys = {post_step_key(p) for p in platforms_for_posts}
        existing_post_steps = db.fetch_all(
            """
            SELECT step_key, status FROM collect_phase_steps
            WHERE task_id=%s AND parent_step_key=%s
            """,
            (task_id, POST_PARENT_STEP_KEY),
        )
        for row in existing_post_steps:
            step_key = str(row.get("step_key") or "")
            if not step_key or step_key in keep_keys:
                continue
            status = str(row.get("status") or "")
            if status not in {"completed", "skipped"}:
                self.set_step_status(step_key=step_key, task_id=task_id, status="skipped", message="未纳入可信账号，跳过该平台发文采集")
        self.set_step_status(
            task_id,
            "step5_validated",
            "completed",
            message=f"已收敛 {count} 个可信账号",
            payload={"validated_count": count, "platforms": platforms_for_posts},
        )

    def finalize_task(self, task_id: str) -> None:
        from collect_01.step_reconcile import reconcile_stuck_pipeline

        task = self.get_task(task_id) or {}
        if str(task.get("status") or "") == "failed":
            return

        # 先收口发文子步骤（含 Apify 失败），再走门禁推进文本流，避免被 running 子步骤堵死
        _reconcile_expand_post_child_steps(self, task_id)
        reconcile_stuck_pipeline(self, task_id)
        _reconcile_expand_post_child_steps(self, task_id)

        profiles = db.fetch_one(
            "SELECT COUNT(*) AS c FROM collect_profiles WHERE task_id=%s", (task_id,)
        )
        posts = db.fetch_one(
            "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s", (task_id,)
        )
        validated = db.fetch_one(
            "SELECT COUNT(*) AS c FROM collect_validated_accounts WHERE task_id=%s AND verdict='validated'",
            (task_id,),
        )
        pc = int((profiles or {}).get("c") or 0)
        poc = int((posts or {}).get("c") or 0)
        vc = int((validated or {}).get("c") or 0)
        plats = db.fetch_all(
            "SELECT DISTINCT platform FROM collect_validated_accounts WHERE task_id=%s AND verdict='validated'",
            (task_id,),
        )
        platforms = [r["platform"] for r in plats]
        step2 = _step_status(task_id, "step2_cross_platform")
        step5 = _step_status(task_id, "step5_validated")
        step2_ok = step2 in {"completed", "skipped"}
        step5_ok = step5 in {"completed", "skipped"}
        post_children = _expand_post_step_rows(task_id)
        post_children_ok = all(str(r.get("status") or "") in {"completed", "skipped", "failed"} for r in post_children)
        # 历史遗留 step6_posts(2.9) 直接删除，不再 skip 占位
        if _step_status(task_id, "step6_posts"):
            db.execute(
                "DELETE FROM collect_phase_steps WHERE task_id=%s AND step_key='step6_posts'",
                (task_id,),
            )
        ready_done = step2_ok and step5_ok and (poc > 0 or post_children_ok or not post_children)
        db.execute(
            """
            INSERT INTO collect_task_summaries
              (task_id, validated_account_count, profile_count, post_count, platforms_json, summary_text)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              validated_account_count=VALUES(validated_account_count),
              profile_count=VALUES(profile_count),
              post_count=VALUES(post_count),
              platforms_json=VALUES(platforms_json),
              summary_text=VALUES(summary_text),
              generated_at=NOW(3)
            """,
            (
                task_id,
                vc,
                pc,
                poc,
                db.json_dumps(platforms),
                (
                    f"扩建完成：{vc} 个可信账号，{pc} 条资料，{poc} 条发文"
                    if ready_done
                    else f"扩建未收口：step2={step2}，step5={step5}，发文子步骤未结束，当前 {pc} 条资料，{poc} 条发文"
                ),
            ),
        )
        if ready_done:
            db.execute(
                "UPDATE hermes_tasks SET status='completed', current_phase=%s, finished_at=COALESCE(finished_at, NOW(3)) WHERE task_id=%s AND status NOT IN ('failed')",
                (PHASE_DONE, task_id),
            )
        else:
            current_phase = PHASE_ACCOUNT_FINALIZE if not step5_ok else PHASE_COLLECT
            db.execute(
                "UPDATE hermes_tasks SET status='running', current_phase=%s, finished_at=NULL, updated_at=NOW(3) WHERE task_id=%s AND status NOT IN ('failed', 'completed')",
                (current_phase, task_id),
            )
