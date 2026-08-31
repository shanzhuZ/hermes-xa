"""04 账号画像写报 — 任务与步骤状态写入。"""

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
from report_04.phases import (
    ANALYSIS_STEP_KEYS,
    PHASE_ANALYSIS,
    PHASE_DISCOVERY,
    PHASE_DONE,
    PHASE_POSTS,
    PHASE_PROFILES,
    PHASE_REPORT,
    PHASE_SEED,
    PHASE_STREAM_VALIDATE,
    PHASE_VALIDATED,
    POST_PARENT_STEP_KEY,
    PROFILE_PARENT_STEP_KEY,
    STEP_PLAN_KEY,
    TASK_TYPE,
    PLATFORM_LABELS,
    direct_execution_children,
    immediate_parent_step_key,
    initial_steps,
    is_phase_shell,
    phase_shell_of_execution_step,
    post_platform_step_key,
    profile_platform_step_key,
    post_step_node,
    post_step_order,
    post_step_title,
    profile_step_node,
    profile_step_order,
    profile_step_title,
    root_steps_for_platform,
    step_phase,
    is_collectible_platform,
)
from report_04.gates import get_step_status, discovery_steps_terminal
from report_04.report_parser import is_final_report

from common.source_tag import source_tag_json

logger = logging.getLogger(__name__)

_REPORT_INTENT = re.compile(
    r"(account-intelligence-report|account-intelligence-profile|画像写报|写报.*?(推特|twitter|微博|weibo|@))",
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


def is_report_intent(user_message: str) -> bool:
    return bool(_REPORT_INTENT.search(user_message or ""))


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


def _report_post_step_rows(task_id: str) -> List[Dict[str, Any]]:
    return db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
          AND step_key LIKE %s AND step_key <> 'step7_posts'
        ORDER BY step_order, step_key
        """,
        (task_id, POST_PARENT_STEP_KEY, "step7_post_%"),
    )


def _reconcile_report_post_child_steps(store: "TaskStore", task_id: str) -> int:
    """会话结束：按发文入库结果收口步骤七子节点。未尝试采集前不 skip（尤其种子平台）。"""
    from report_04.step_reconcile import (
        _post_actor_without_dataset,
        _post_collect_attempted,
        _task_seed_platform,
    )

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
    seed_plat = _task_seed_platform(task_id)
    updated = 0
    for row in _report_post_step_rows(task_id):
        step_key = str(row.get("step_key") or "")
        if not step_key.startswith("step7_post_"):
            continue
        platform = step_key.replace("step7_post_", "", 1)
        cur = str(row.get("status") or "")
        if cur in {"completed", "skipped", "failed"}:
            continue
        cnt = post_counts.get(platform, 0)
        if cnt > 0:
            from report_04.gates import is_post_tool_inflight
            from report_04.video_job import finalize_post_platform_after_posts

            # 工具在飞：禁止边入边完
            if is_post_tool_inflight(task_id, platform):
                if cur != "running":
                    store.set_step_status(
                        task_id,
                        step_key,
                        "running",
                        message=f"{platform} 发文工具执行中（已入库 {cnt} 条，待工具返回）",
                    )
                    updated += 1
                continue
            finalize_post_platform_after_posts(
                store, task_id, platform, post_count=cnt
            )
            updated += 1
            continue
        # 旧版「等视频」钉 running：视频终态后补 completed，并尝试关父壳
        if cur == "running":
            from report_04.gates import is_post_tool_inflight
            from report_04.video_job import complete_post_after_video

            if is_post_tool_inflight(task_id, platform):
                continue
            before = cur
            complete_post_after_video(store, task_id, platform)
            if str(get_step_status(task_id, step_key) or "") != before:
                updated += 1
                continue
        attempted = _post_collect_attempted(task_id, platform)
        if not attempted:
            if _post_actor_without_dataset(task_id, platform):
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"{platform} Actor 已完成但未拉取发文 dataset",
                )
                updated += 1
            # 未调用发文工具：会话收口也不再空 skip（保持 pending，由 close_open 统一处理）
            # 避免「违规空过 + 父 completed」掩盖未采集
            continue
        if platform in validated or platform == seed_plat:
            store.set_step_status(
                task_id,
                step_key,
                "skipped",
                message=f"{platform} 已调用发文工具但未采集到发文",
            )
            updated += 1
    # 仅当无「未尝试」残留时才关父节点
    if updated:
        from report_04.step_reconcile import close_collect_parent_if_ready

        close_collect_parent_if_ready(store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕")
    return updated


class TaskStore:
    """04 账号画像写报入库门面。"""

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

    def get_latest_task_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """同 session 最近一条写报任务（含 completed），用于防止 Hook 重放新建幽灵任务。"""
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
            WHERE task_id=%s AND status NOT IN ('failed', 'completed', 'cancelled')
            """,
            (error_message[:2000], task_id),
        )

    def fail_seed_and_abort(self, task_id: str, error_message: str) -> None:
        """种子主页采集失败：步骤一 failed、任务 failed，未完成步骤一律 skipped。"""
        msg = (error_message or "种子账号采集失败，请检查账号名").strip()
        cur1 = get_step_status(task_id, "step1_seed")
        if cur1 not in {"completed", "failed", "skipped"}:
            self.set_step_status(task_id, "step1_seed", "failed", message=msg[:500])
        elif cur1 != "failed":
            # 已 completed 但业务上应失败时不再改步骤终态语义；任务级失败仍写入
            pass
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
        if not is_report_intent(user_message):
            raise ValueError("非画像写报意图消息，无法创建任务")
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
        initial_phase_steps = initial_steps(seed.get("platform"))
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
                        PHASE_SEED,
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
                          (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (
                            task_id,
                            step.step_key,
                            step.parent_step_key,
                            step.step_order,
                            step.step_node,
                            step.title,
                            source_tag_json(step.step_key, seed_platform=seed.get("platform")),
                        ),
                    )
        logger.info("预建写报任务 task_id=%s session=%s", task_id, session_id)
        return task_id

    def ensure_task(
        self,
        *,
        session_id: str,
        user_message: str,
        task_id: Optional[str] = None,
    ) -> Optional[str]:
        if not is_report_intent(user_message):
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
        # 同 session 已有任务（含刚 completed）：禁止因 Hook 重放 user_message 再建幽灵任务
        # 新用户请求应由 Java 换新 session_id / 预插新 task_id
        latest = self.get_latest_task_by_session(session_id) if session_id else None
        if latest:
            logger.info(
                "复用同 session 已有写报任务 task=%s status=%s（跳过新建）",
                latest.get("task_id"),
                latest.get("status"),
            )
            return str(latest["task_id"])

        new_id = task_id or str(uuid.uuid4())
        seed = _parse_seed(user_message)
        cross_platform = 1
        initial_phase_steps = initial_steps(seed.get("platform"))
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
                        PHASE_SEED,
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
                          (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (
                            new_id,
                            step.step_key,
                            step.parent_step_key,
                            step.step_order,
                            step.step_node,
                            step.title,
                            source_tag_json(step.step_key, seed_platform=seed.get("platform")),
                        ),
                    )
        # 建树保持 pending；业务步由 sink/工具触发后再 running（与 Java 规划门闩一致）
        logger.info("创建写报任务 task_id=%s session=%s", new_id, session_id)
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

    def _upsert_summary_dialogue(
        self,
        task_id: str,
        session_id: Optional[str],
        content: str,
        tags_json: Optional[str],
        *,
        existing_id: Optional[int] = None,
    ) -> None:
        """写入/更新 summary 行；report_tags 列不存在时降级为仅写 content。"""
        if existing_id is not None:
            sql_with_tags = """
                UPDATE hermes_user_dialogues
                SET session_id=%s, role='assistant', content=%s, report_tags=%s, created_at=NOW(3)
                WHERE id=%s
            """
            sql_no_tags = """
                UPDATE hermes_user_dialogues
                SET session_id=%s, role='assistant', content=%s, created_at=NOW(3)
                WHERE id=%s
            """
            args_with = (session_id, content, tags_json, existing_id)
            args_no = (session_id, content, existing_id)
        else:
            sql_with_tags = """
                INSERT INTO hermes_user_dialogues
                    (task_id, session_id, role, content, msg_type, report_tags)
                VALUES (%s, %s, 'assistant', %s, 'summary', %s)
            """
            sql_no_tags = """
                INSERT INTO hermes_user_dialogues
                    (task_id, session_id, role, content, msg_type)
                VALUES (%s, %s, 'assistant', %s, 'summary')
            """
            args_with = (task_id, session_id, content, tags_json)
            args_no = (task_id, session_id, content)
        try:
            db.execute(sql_with_tags, args_with)
        except Exception as exc:
            if tags_json and "report_tags" in str(exc).lower():
                logger.warning(
                    "report_tags 列不可用，降级仅写 summary 正文 task=%s: %s",
                    task_id,
                    exc,
                )
                db.execute(sql_no_tags, args_no)
            else:
                raise

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
        is_report = is_final_report(text)
        report_tags: List[str] = []
        if is_report:
            from report_04.report_parser import (
                extract_report_body,
                extract_report_tags,
                sanitize_report_dirty_meta,
            )
            from report_04.video_report import (
                can_write_report_after_videos,
                inject_video_into_report,
            )

            gate = can_write_report_after_videos(task_id)
            if not gate.get("ok"):
                logger.warning(
                    "画像终稿被写报门禁拦截（发文或视频未齐） task=%s open=%s",
                    task_id,
                    gate.get("open"),
                )
                self.save_dialogue(
                    task_id,
                    session_id,
                    "assistant",
                    text[:65535],
                    msg_type="assistant_reply",
                )
                return False
            raw_body = sanitize_report_dirty_meta(extract_report_body(text))
            final_body, report_tags = extract_report_tags(raw_body)
            text = inject_video_into_report(final_body, task_id)
        clipped = text[:65535]
        tags_json = (
            json.dumps(report_tags, ensure_ascii=False) if is_report and report_tags else None
        )
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
                self._upsert_summary_dialogue(
                    task_id,
                    session_id,
                    clipped,
                    tags_json,
                    existing_id=int(existing["id"]),
                )
                logger.info(
                    "已更新写报终稿 task=%s len=%d tags=%s",
                    task_id,
                    len(text),
                    report_tags or [],
                )
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
        if is_report:
            self._upsert_summary_dialogue(
                task_id, session_id, clipped, tags_json, existing_id=None
            )
        else:
            self.save_dialogue(task_id, session_id, "assistant", text, msg_type)
        logger.info(
            "已保存助手输出 task=%s type=%s len=%d tags=%s",
            task_id,
            msg_type,
            len(text),
            report_tags if is_report else [],
        )
        return True

    def load_user_input_message(self, task_id: str) -> str:
        row = db.fetch_one(
            """
            SELECT content FROM hermes_user_dialogues
            WHERE task_id=%s AND msg_type='user_input'
            ORDER BY id ASC LIMIT 1
            """,
            (task_id,),
        )
        return str((row or {}).get("content") or "")

    def get_seed_accounts(self, task_id: str) -> List[Dict[str, Any]]:
        task = self.get_task(task_id) or {}
        seed: Dict[str, Any] = {}
        try:
            seed = json.loads(task.get("seed_json") or "{}")
        except json.JSONDecodeError:
            seed = {}
        stored = seed.get("seed_accounts")
        if isinstance(stored, list) and stored:
            return stored
        rows = db.fetch_all(
            """
            SELECT platform, account_id, account_handle
            FROM collect_profiles WHERE task_id=%s
            ORDER BY collected_at ASC
            """,
            (task_id,),
        )
        if rows:
            return [dict(r) for r in rows]
        platform = str(seed.get("platform") or "twitter")
        handle = str(seed.get("account_handle") or seed.get("account_hint") or "").strip()
        if handle:
            return [{"platform": platform, "account_handle": handle, "account_id": handle}]
        return []

    def mark_seed_completed(
        self,
        task_id: str,
        accounts: List[Dict[str, Any]],
        *,
        source: str = "agent",
    ) -> None:
        if get_step_status(task_id, "step1_seed") == "completed":
            return
        task = self.get_task(task_id) or {}
        seed: Dict[str, Any] = {}
        try:
            seed = json.loads(task.get("seed_json") or "{}")
        except json.JSONDecodeError:
            seed = {}
        if accounts:
            seed["seed_accounts"] = accounts
            first = accounts[0]
            if first.get("platform"):
                seed["platform"] = first["platform"]
            if first.get("account_handle"):
                seed["account_handle"] = first["account_handle"]
            seed["parse_status"] = "completed"
            db.execute(
                "UPDATE hermes_tasks SET seed_json=%s, updated_at=NOW(3) WHERE task_id=%s",
                (db.json_dumps(seed), task_id),
            )
        platforms = sorted(
            {str(a.get("platform") or "") for a in (accounts or self.get_seed_accounts(task_id)) if a.get("platform")}
        )
        self.set_step_status(
            task_id,
            "step1_seed",
            "completed",
            message="种子 profile 已确认" if source != "tool" else "种子 profile 已入库",
            payload={"source": source, "account_count": len(accounts or [])},
        )
        self.set_task_phase(task_id, PHASE_DISCOVERY)

    def reconcile_collect_child_steps(self, task_id: str) -> int:
        from report_04.step_reconcile import (
            close_collect_parent_if_ready,
            ensure_osint_not_premature,
            ensure_step4_parent_not_premature,
            ensure_step5_not_premature,
            ensure_step7_awaits_agent_tool,
            ensure_step7_parent_not_premature,
            maybe_close_abandoned_step4,
            reconcile_step4_and_step7_children,
        )

        # 有主页进展超时才 fail-forward；抢跑 4.3 时不 skip
        updated = maybe_close_abandoned_step4(self, task_id, min_quiet_seconds=60.0, force=False)
        updated += reconcile_step4_and_step7_children(self, task_id)
        updated += ensure_step4_parent_not_premature(self, task_id)
        updated += ensure_osint_not_premature(self, task_id)
        updated += ensure_step7_parent_not_premature(self, task_id)
        updated += ensure_step7_awaits_agent_tool(self, task_id)
        for parent, msg_done in (
            (PROFILE_PARENT_STEP_KEY, "候选主页采集已尝试完毕"),
            (POST_PARENT_STEP_KEY, "发文采集已尝试完毕"),
        ):
            updated += close_collect_parent_if_ready(self, task_id, parent, msg_done)
        # 步骤四仍未终态时，回滚 Java 粗同步误点的步骤五
        updated += ensure_step5_not_premature(self, task_id)
        try:
            from report_04.session_continue import maybe_nudge_stalled_analysis

            maybe_nudge_stalled_analysis(self, task_id, min_quiet_seconds=90.0)
        except Exception:
            pass
        return updated

    def _seed_platform(self, task_id: str) -> str:
        task = self.get_task(task_id) or {}
        try:
            seed = json.loads(task.get("seed_json") or "{}")
        except json.JSONDecodeError:
            seed = {}
        return str((seed or {}).get("platform") or "twitter")

    def init_phase_steps(self, task_id: str) -> None:
        seed_plat = self._seed_platform(task_id)
        for step in root_steps_for_platform(seed_plat):
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    step.step_key,
                    step.parent_step_key,
                    step.step_order,
                    step.step_node,
                    step.title,
                    source_tag_json(step.step_key, seed_platform=seed_plat),
                ),
            )

    def ensure_profile_steps(self, task_id: str, platforms: List[str]) -> None:
        parent = PROFILE_PARENT_STEP_KEY
        for platform in sorted(set(platforms), key=profile_step_order):
            key = profile_platform_step_key(platform)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    key,
                    parent,
                    profile_step_order(platform),
                    profile_step_node(platform),
                    profile_step_title(platform),
                    source_tag_json(key),
                ),
            )

    def materialize_step4_from_candidates(self, task_id: str) -> List[str]:
        """步骤二+三候选按平台去重后，统一生成步骤四子节点。"""
        if not discovery_steps_terminal(task_id):
            return []
        rows = db.fetch_all(
            """
            SELECT platform, account_handle, match_strategy FROM cross_platform_candidates
            WHERE task_id=%s AND platform IS NOT NULL AND TRIM(platform) != ''
            """,
            (task_id,),
        )
        seed_handle = ""
        task = self.get_task(task_id) or {}
        try:
            seed = json.loads(task.get("seed_json") or "{}")
            seed_handle = str(seed.get("account_handle") or seed.get("account_hint") or "").strip()
        except json.JSONDecodeError:
            pass
        from report_04.candidate_parser import relevant_profile_platforms

        platforms = relevant_profile_platforms(rows, seed_handle)
        if not platforms:
            seed = self.get_seed_accounts(task_id)
            platforms = sorted(
                {
                    str(a.get("platform") or "")
                    for a in seed
                    if a.get("platform") and is_collectible_platform(str(a.get("platform")))
                },
                key=profile_step_order,
            )
        if platforms:
            self.ensure_profile_steps(task_id, platforms)
        relevant_set = set(platforms)
        # 跳过无采集通道 / 线索发现无该平台候选的子节点（不做种子相似度过滤）
        for row in db.fetch_all(
            "SELECT step_key FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
            (task_id, PROFILE_PARENT_STEP_KEY),
        ):
            sk = str(row.get("step_key") or "")
            if not sk.startswith("step4_profile_"):
                continue
            plat = sk.replace("step4_profile_", "", 1)
            cur = get_step_status(task_id, sk)
            if not is_collectible_platform(plat):
                if cur not in {"completed", "skipped", "failed"}:
                    self.set_step_status(task_id, sk, "skipped", message=f"{plat} 无可用主页采集工具")
            elif plat not in relevant_set and cur in {"pending", "running"}:
                self.set_step_status(
                    task_id,
                    sk,
                    "skipped",
                    message=f"{plat} 线索发现无候选，跳过",
                )
            # skipped 为终态：禁止改回 pending 补采（无论当初为何 skip）
        parent = PROFILE_PARENT_STEP_KEY
        # 仅物化子节点；父步骤等首个主页采集工具再 running，避免步骤3 web 尚未停就假 running
        if get_step_status(task_id, parent) is None:
            self.ensure_step_row(task_id, parent)
        from report_04.step_reconcile import reconcile_step4_and_step7_children

        reconcile_step4_and_step7_children(self, task_id)
        return platforms

    def ensure_post_steps(self, task_id: str, platforms: List[str]) -> None:
        parent = POST_PARENT_STEP_KEY
        for platform in sorted(set(platforms), key=post_step_order):
            key = post_platform_step_key(platform)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    key,
                    parent,
                    post_step_order(platform),
                    post_step_node(platform),
                    post_step_title(platform),
                    source_tag_json(key),
                ),
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
        touch_updated_at: bool = True,
        force_reopen: bool = False,
        skip_phase_rollup: bool = False,
    ) -> None:
        self.ensure_step_row(task_id, step_key)
        current = db.fetch_one(
            "SELECT status, finished_at, payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
            (task_id, step_key),
        )
        cur_status = str((current or {}).get("status") or "")

        # skipped 终态：禁止回 pending/running（含 force_reopen）。允许升为 completed/failed。
        if cur_status == "skipped" and status in {"pending", "running"}:
            logger.info(
                "拒绝 skipped→%s step=%s task=%s",
                status,
                step_key,
                task_id,
            )
            return

        # 父步骤四一旦 completed，默认禁止再打回 running；晚到补采可 force_reopen
        if (
            step_key == PROFILE_PARENT_STEP_KEY
            and status == "running"
            and cur_status == "completed"
            and not force_reopen
        ):
            logger.info("拒绝 step4_profiles completed→running task=%s", task_id)
            return
        # 步骤七父节点同理：子步骤全终态收口后禁止粗/误写再点亮
        if (
            step_key == POST_PARENT_STEP_KEY
            and status == "running"
            and cur_status == "completed"
            and not force_reopen
        ):
            logger.info("拒绝 step7_posts completed→running task=%s", task_id)
            return
        # 4.1 父壳：两子收口后禁止再被 vision 等打回 running
        if (
            step_key == "step5_streams"
            and status == "running"
            and cur_status == "completed"
            and not force_reopen
        ):
            logger.info("拒绝 step5_streams completed→running task=%s", task_id)
            return
        # [COLLISION_DEMO_FAKE] 假节点完成后禁止改回 pending/running — 正式版删除本段
        try:
            from report_04.phases import is_collision_demo_step

            if (
                is_collision_demo_step(step_key)
                and cur_status in {"completed", "skipped", "failed"}
                and status in {"pending", "running"}
                and not force_reopen
            ):
                logger.info(
                    "[COLLISION_DEMO_FAKE] 拒绝 %s %s→%s task=%s",
                    step_key,
                    cur_status,
                    status,
                    task_id,
                )
                return
        except Exception:
            pass
        # [COLLISION_DEMO_FAKE] end

        # 软顺序：确认本步会写成 running 后，再点亮所属七大壳（避免拒绝回开时误亮父壳）
        if (
            not skip_phase_rollup
            and status == "running"
            and not is_phase_shell(step_key)
        ):
            self._ensure_phase_shell_running(task_id, step_key)

        fields = ["status=%s"]
        params: List[Any] = [status]
        if touch_updated_at:
            fields.append("updated_at=NOW(3)")
        if status == "running":
            fields.append("started_at=COALESCE(started_at, NOW(3))")
            # skipped 已在入口拦截；此处只清 completed/failed 回开后的 finished_at
            if cur_status in {"completed", "failed", "skipped"} and step_key != PROFILE_PARENT_STEP_KEY:
                fields.append("finished_at=NULL")
            elif cur_status in {"completed", "failed", "skipped"} and step_key == PROFILE_PARENT_STEP_KEY:
                fields.append("finished_at=NULL")
        elif status == "pending":
            fields.append("finished_at=NULL")
            if cur_status in {"completed", "failed", "skipped", "running"}:
                fields.append("started_at=NULL")
        elif status in {"completed", "failed", "skipped"}:
            # 终态时间只写一次，避免后续流程把 finished_at 往后推造成「假顺序」
            fields.append("finished_at=COALESCE(finished_at, NOW(3))")
            # pending→终态也补 started_at，避免前端看不到 running 时段
            if cur_status in {"pending", ""}:
                fields.append("started_at=COALESCE(started_at, NOW(3))")
        if message is not None:
            fields.append("message=%s")
            params.append(message[:2000])
        if payload is not None:
            # 合并 payload，保留 wait_images_since 等计时字段
            merged = {}
            try:
                merged = json.loads((current or {}).get("payload_json") or "{}")
            except Exception:
                merged = {}
            if not isinstance(merged, dict):
                merged = {}
            merged.update(payload)
            fields.append("payload_json=%s")
            params.append(db.json_dumps(merged))
        if progress_pct is not None:
            fields.append("progress_pct=%s")
            params.append(progress_pct)
        params.extend([task_id, step_key])
        db.execute(
            f"UPDATE collect_phase_steps SET {', '.join(fields)} WHERE task_id=%s AND step_key=%s",
            tuple(params),
        )
        try:
            phase = step_phase(step_key)
            # 仅 running/completed 推进 current_phase；pending 回滚不改 phase（避免步骤7误抢 phase=posts）
            if phase and status in {"running", "completed"}:
                self.set_task_phase(task_id, phase)
        except Exception as exc:
            logger.warning(
                "set_task_phase 失败 task=%s step=%s status=%s: %s",
                task_id,
                step_key,
                status,
                exc,
            )

        # 业务直接子步终态后：若同壳下直接子步均终态，则父壳 completed
        if (
            not skip_phase_rollup
            and status in {"completed", "skipped", "failed"}
            and not is_phase_shell(step_key)
        ):
            try:
                self._maybe_complete_phase_shell(task_id, step_key)
            except Exception as exc:
                logger.warning(
                    "phase shell 收口失败 task=%s step=%s: %s",
                    task_id,
                    step_key,
                    exc,
                )

    def _lookup_parent_step_key(self, task_id: str, step_key: str) -> Optional[str]:
        """查库 parent_step_key，缺失时回落约定映射。"""
        row = db.fetch_one(
            "SELECT parent_step_key FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
            (task_id, step_key),
        )
        parent = str((row or {}).get("parent_step_key") or "").strip()
        if parent:
            return parent
        return immediate_parent_step_key(step_key)

    def _parent_chain_to_phase_shell(self, task_id: str, step_key: str) -> List[str]:
        """从直接父向上到七大壳（含壳，不含 step_plan），近→远。

        例：step7_post_twitter → [step7_posts, phase_content]
            step5_stream_text → [step5_streams, phase_collision]
            step7_posts → [phase_content]
        """
        chain: List[str] = []
        seen = set()
        cur = self._lookup_parent_step_key(task_id, step_key)
        while cur and cur not in seen and cur != STEP_PLAN_KEY:
            seen.add(cur)
            chain.append(cur)
            if is_phase_shell(cur):
                break
            cur = self._lookup_parent_step_key(task_id, cur)
        return chain

    def _ensure_phase_shell_running(self, task_id: str, execution_step_key: str) -> None:
        """业务步变 running 时，沿 parent 链点亮中间父与七大壳（深叶也生效）。

        解决 step7_post_* / step4_profile_* 等深叶不在 EXECUTION_PARENT_SHELL 时，
        父节点与 phase_* 长时间停在 pending 的空窗。
        """
        from report_04.phases import (
            PHASE_COLLISION,  # [COLLISION_DEMO_FAKE] 触发用
            is_post_platform_step,
            is_video_platform_step,
        )

        chain = self._parent_chain_to_phase_shell(task_id, execution_step_key)
        if not chain:
            # 兼容：仅映射到壳的旧路径
            shell = phase_shell_of_execution_step(execution_step_key)
            chain = [shell] if shell else []
        for node in chain:
            # 方案 A：视频旁路不把已 completed 的发文子步重新钉成 running；
            # 仍点亮 step7_posts / phase_content，保证树上层与视频进行中一致
            if is_video_platform_step(execution_step_key) and is_post_platform_step(node):
                continue
            cur = get_step_status(task_id, node)
            if cur == "skipped":
                continue
            if cur == "running":
                # [COLLISION_DEMO_FAKE] 壳已 running 也尝试幂等拉起假节点
                if node == PHASE_COLLISION:
                    self._kickoff_collision_demo_fake(task_id)
                continue
            # pending / 空 / completed（补采重开）→ running；skipped 终态不点亮
            force = cur == "completed"
            self.set_step_status(
                task_id,
                node,
                "running",
                message="子步骤执行中（晚到补采）" if force else "子步骤执行中",
                skip_phase_rollup=True,
                force_reopen=force,
            )
            # [COLLISION_DEMO_FAKE] 「4. 关联碰撞」变 running → 启动 4.4/4.5/4.6 假节点
            if node == PHASE_COLLISION:
                self._kickoff_collision_demo_fake(task_id)

    def _kickoff_collision_demo_fake(self, task_id: str) -> None:
        """[COLLISION_DEMO_FAKE] 正式版删除本方法及所有调用点。"""
        try:
            from report_04.collision_demo_steps import reconcile_collision_demo_steps

            reconcile_collision_demo_steps(self, task_id)
        except Exception as exc:
            logger.warning("[COLLISION_DEMO_FAKE] kickoff/heal 失败 task=%s: %s", task_id, exc)

    def _maybe_complete_phase_shell(self, task_id: str, execution_step_key: str) -> None:
        """业务步终态后收口父节点/七大壳。

        - 直接业务步（如 step7_posts）：收口其映射父（phase_content）
        - 深叶（如 step7_post_*）：映射为空，仍向上尝试收口已终态的七大壳
          （修复：壳被深叶点亮后，仅关 step7_posts 时漏收口导致 phase_content 永久 running）
        """
        mapped = phase_shell_of_execution_step(execution_step_key)
        if mapped:
            self._try_complete_parent_if_children_done(task_id, mapped)
            # mapped 可能是中间父（如 step5_streams），继续向七大壳收口
            if not is_phase_shell(mapped):
                shell_parent = self._lookup_parent_step_key(task_id, mapped)
                if shell_parent and is_phase_shell(shell_parent):
                    self._try_complete_parent_if_children_done(task_id, shell_parent)
            return
        # 深叶：沿父链找到七大壳再尝试收口
        parent = self._lookup_parent_step_key(task_id, execution_step_key)
        seen = set()
        while parent and parent not in seen and parent != STEP_PLAN_KEY:
            seen.add(parent)
            if is_phase_shell(parent):
                self._try_complete_parent_if_children_done(task_id, parent)
                return
            parent = self._lookup_parent_step_key(task_id, parent)

    def _try_complete_parent_if_children_done(self, task_id: str, parent: str) -> bool:
        """若 parent 下直接子步均已终态，则标 completed。返回是否已是终态。"""
        from report_04.phases import (
            COLLISION_DEMO_STEP_KEYS,  # [COLLISION_DEMO_FAKE]
            PHASE_COLLISION,
            PHASE_CONTENT,
            POST_PARENT_STEP_KEY,
            is_collision_demo_step,  # [COLLISION_DEMO_FAKE]
        )

        cur = get_step_status(task_id, parent)
        if cur in {"completed", "skipped"}:
            return True
        # 方案 A：发文父壳 / 内容采集壳须等视频孙节点终态（发文子步可先 completed）
        if parent in {POST_PARENT_STEP_KEY, PHASE_CONTENT}:
            try:
                from report_04.video_report import video_steps_terminal

                vt = video_steps_terminal(task_id)
                if not vt.get("ok"):
                    return False
            except Exception as exc:
                logger.warning(
                    "视频终态检查失败，暂不收口 %s task=%s: %s", parent, task_id, exc
                )
                return False
        rows = db.fetch_all(
            "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
            (task_id, parent),
        )
        statuses: List[str] = []
        if rows:
            for r in rows:
                sk = str(r.get("step_key") or "")
                # [COLLISION_DEMO_FAKE] 壳「4. 关联碰撞」收口不等 4.4/4.5/4.6
                if parent == PHASE_COLLISION and sk in COLLISION_DEMO_STEP_KEYS:
                    continue
                if is_collision_demo_step(sk) and parent == PHASE_COLLISION:
                    continue
                statuses.append(str(r.get("status") or ""))
        else:
            for child in direct_execution_children(parent):
                # [COLLISION_DEMO_FAKE]
                if parent == PHASE_COLLISION and child in COLLISION_DEMO_STEP_KEYS:
                    continue
                st = get_step_status(task_id, child)
                statuses.append(st or "")
        if not statuses:
            return False
        if any(st not in {"completed", "skipped", "failed"} for st in statuses):
            return False
        msg = (
            "阶段内业务步骤已全部终态"
            if is_phase_shell(parent)
            else "子步骤已全部终态"
        )
        self.set_step_status(
            task_id,
            parent,
            "completed",
            message=msg,
            skip_phase_rollup=True,
        )
        return True

    def ensure_step_row(self, task_id: str, step_key: str) -> None:
        """旧任务可能缺少 step4_profiles 等新步骤行。"""
        seed_plat = self._seed_platform(task_id)
        for step in root_steps_for_platform(seed_plat):
            if step.step_key == step_key:
                db.execute(
                    """
                    INSERT IGNORE INTO collect_phase_steps
                      (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                    """,
                    (
                        task_id,
                        step.step_key,
                        step.parent_step_key,
                        step.step_order,
                        step.step_node,
                        step.title,
                        source_tag_json(step.step_key, seed_platform=seed_plat),
                    ),
                )
                return
        if step_key.startswith("step7_post_"):
            platform = step_key.replace("step7_post_", "", 1)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    step_key,
                    POST_PARENT_STEP_KEY,
                    post_step_order(platform),
                    post_step_node(platform),
                    post_step_title(platform),
                    source_tag_json(step_key),
                ),
            )
            return
        if step_key.startswith("step7_video_"):
            from report_04.phases import (
                video_step_node,
                video_step_order,
                video_step_title,
            )

            platform = step_key.replace("step7_video_", "", 1)
            parent = post_platform_step_key(platform)
            # 先确保发文父步存在
            self.ensure_step_row(task_id, parent)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    step_key,
                    parent,
                    video_step_order(platform),
                    video_step_node(platform),
                    video_step_title(platform),
                    source_tag_json(step_key),
                ),
            )
            return
        if step_key.startswith("step4_profile_"):
            platform = step_key.replace("step4_profile_", "", 1)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    step_key,
                    PROFILE_PARENT_STEP_KEY,
                    profile_step_order(platform),
                    profile_step_node(platform),
                    profile_step_title(platform),
                    source_tag_json(step_key),
                ),
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

    def save_profile_row(self, row: Dict[str, Any], *, step_key: str = "step4_profiles") -> None:
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

    def save_candidate_rows(self, rows: List[Dict[str, Any]], *, step_key: str = "step2_maigret") -> None:
        for row in rows:
            conf = row.get("confidence")
            if isinstance(conf, str):
                conf_map = {"high": 0.9, "medium": 0.6, "low": 0.3}
                conf = conf_map.get(conf.lower(), None)
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
                    conf,
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
        from collect_01.normalizers.base import normalize_published_at

        for row in rows:
            row = {**row, "published_at": normalize_published_at(row.get("published_at"))}
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
                  repost_count=VALUES(repost_count), media_json=VALUES(media_json),
                  raw_json=VALUES(raw_json),
                  published_at=COALESCE(VALUES(published_at), published_at)
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

                sync_stream_display(sid, step_key="step5_streams")
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

                sync_stream_display(sid, step_key="step5_streams")
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
            # 未命中库内图片流：常见于对非 payload_url 的 CDN 乱调 vision/OCR
            logger.info(
                "图片流未匹配 task=%s tool=%s src=%s",
                task_id,
                tool_name,
                (_extract_image_source(tool_args) or "")[:120],
            )
            return False
        stream_id = str(stream.get("stream_id") or "")
        if not stream_id:
            return False
        if tool_name == "mcp_ocr_perform_ocr":
            # OCR 成败都不结束图片流（须等 vision）；无字/失败均提示跳过 OCR 继续 vision，禁止盲重试
            detail = (
                "OCR 完成，等待 vision"
                if success
                else "OCR 失败或无字，跳过 OCR 立即 vision（禁止同参重试）"
            )
            db.execute(
                """
                UPDATE collect_identity_streams
                SET validation_detail=%s, updated_at=NOW(3)
                WHERE stream_id=%s AND validation_status='pending'
                """,
                (detail, stream_id),
            )
            return True
        # vision：成功→processed；失败→fail（含远程 URL 403）。
        # 旧逻辑对远程失败只写 detail 保持 pending「等本地重试」，模型不重试则步骤5永卡。
        status = "processed" if success else "fail"
        detail = "vision 分析完成" if success else "vision 分析失败"
        if not success:
            source = _extract_image_source(tool_args)
            if source.startswith(("http://", "https://")):
                detail = "vision 远程 URL 失败（已终态，勿无限等待本地重试）"
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

            sync_stream_display(stream_id, step_key="step5_streams")
        except Exception as exc:
            logger.warning("展示层双写 stream 失败: %s", exc)
        return True

    def mark_remaining_image_streams_failed(self, task_id: str, detail: str) -> int:
        """图片流仍未处理时兜底标记失败，避免步骤五永久卡住。"""
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

            sync_streams_for_task(task_id, step_key="step5_streams", stream_type="image")
            from report_04.phases import STREAM_IMAGE_STEP_KEY

            sync_streams_for_task(task_id, step_key=STREAM_IMAGE_STEP_KEY, stream_type="image")
        except Exception as exc:
            logger.warning("展示层双写 image streams 失败: %s", exc)
        return n

    def _step5_payload(self, task_id: str) -> Dict[str, Any]:
        """兼容：优先读 4.1.1 payload，否则读父壳。"""
        from report_04.phases import STREAM_TEXT_STEP_KEY

        for key in (STREAM_TEXT_STEP_KEY, "step5_streams"):
            row = db.fetch_one(
                "SELECT payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
                (task_id, key),
            )
            try:
                payload = json.loads((row or {}).get("payload_json") or "{}")
            except Exception:
                payload = {}
            if isinstance(payload, dict) and payload:
                return payload
        return {}

    def run_stream_validation(self, task_id: str) -> None:
        """步骤五：文本流规则比对 → 收口 4.1.1；并触发 4.1.2 图片管线。"""
        from report_04.gates import can_advance_to_step5
        from report_04.phases import STREAM_PARENT_STEP_KEY, STREAM_TEXT_STEP_KEY
        from report_04.stream_steps import (
            ensure_stream_child_steps,
            rollup_step5_parent,
            start_step5_image_pipeline,
        )

        gate = can_advance_to_step5(task_id)
        if not gate.get("ok"):
            logger.info("run_stream_validation 跳过 task=%s: %s", task_id, gate.get("message"))
            return
        task = self.get_task(task_id)
        if not task:
            return

        ensure_stream_child_steps(self, task_id)
        existing = self._step5_payload(task_id)
        text_st = _step_status(task_id, STREAM_TEXT_STEP_KEY)
        # 文本已收口：只补跑图片管线 / rollup
        if existing.get("text_compare_done") and text_st in {"completed", "skipped"}:
            start_step5_image_pipeline(self, task_id)
            rollup_step5_parent(self, task_id)
            return

        self.set_step_status(
            task_id,
            STREAM_PARENT_STEP_KEY,
            "running",
            message="文本/图片流核查中",
        )
        if text_st not in {"completed", "skipped"}:
            self.set_step_status(
                task_id,
                STREAM_TEXT_STEP_KEY,
                "running",
                message="文本流规则比对中",
            )

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
                STREAM_TEXT_STEP_KEY,
                "skipped",
                message=f"种子平台 {seed_platform} 无文本流，跳过文本核验",
                payload={"text_compare_done": True, "matched": 0, "total": 0},
            )
            self.set_task_phase(task_id, PHASE_STREAM_VALIDATE)
            start_step5_image_pipeline(self, task_id)
            rollup_step5_parent(self, task_id)
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

            sync_streams_for_task(task_id, step_key=STREAM_TEXT_STEP_KEY, stream_type="text")
        except Exception as exc:
            logger.warning("展示层双写 text streams 失败: %s", exc)

        total = len(all_text)
        rule_conclusion = f"文本流规则比对完成：通过 {matched}/{total} 条（种子平台 {seed_platform}）。"
        # 系统模型核验：产出「经过…所以…」推理结论与逐条理由
        model_payload: Dict[str, Any] = {}
        try:
            from report_04.stream_verify import run_text_model_verify

            model_payload = run_text_model_verify(task_id) or {}
        except Exception as exc:
            logger.warning("文本模型核验失败 task=%s: %s", task_id, exc)
            model_payload = {"ok": False, "error": str(exc)[:300]}
        conclusion = str(model_payload.get("conclusion") or rule_conclusion)
        source = "model_verify" if model_payload.get("source") == "model_verify" else "rule_compare"
        self.set_step_status(
            task_id,
            STREAM_TEXT_STEP_KEY,
            "completed",
            message=f"文本流核验完成，规则通过 {matched}/{total} 条",
            payload={
                "matched": matched,
                "total": total,
                "text_compare_done": True,
                "conclusion": conclusion,
                "modelAnalysis": str(model_payload.get("modelAnalysis") or conclusion),
                "ruleConclusion": rule_conclusion,
                "source": source,
            },
        )
        self.set_task_phase(task_id, PHASE_STREAM_VALIDATE)
        start_step5_image_pipeline(self, task_id)
        rollup_step5_parent(self, task_id)

    def kickoff_step5_if_ready(self, task_id: str) -> bool:
        """步骤四收口后启动 4.1：文本比对 + 图片管线；父壳等两子终态。"""
        from report_04.gates import can_advance_to_step5
        from report_04.stream_steps import ensure_stream_child_steps, rollup_step5_parent, start_step5_image_pipeline

        gate = can_advance_to_step5(task_id)
        if not gate.get("ok"):
            return False
        ensure_stream_child_steps(self, task_id)
        cur5 = _step_status(task_id, "step5_streams")
        if cur5 not in {"completed", "skipped"}:
            self.run_stream_validation(task_id)
            start_step5_image_pipeline(self, task_id)
            rollup_step5_parent(self, task_id)
        return True

    def run_validated_accounts(self, task_id: str) -> None:
        """步骤六：一次算完并写入可信清单（可重入；展示双写不挡收口）。

        只负责 4.2 收敛落库；4.3 kickoff 由 engine._auto_step5_step6 负责，
        避免把耗时 ES HTTP 塞进本函数后 Hook 超时、且 except 误把 completed 打回 running。
        """
        if _step_status(task_id, "step5_streams") not in {"completed", "skipped"}:
            logger.info("run_validated_accounts 跳过 task=%s: step5_streams 未完成", task_id)
            return
        if _step_status(task_id, "step6_validated") == "completed":
            return
        self.set_step_status(task_id, "step6_validated", "running", message="收敛可信账号…")
        prepared: List[Dict[str, Any]] = []
        platforms_for_posts: List[str] = []
        count = 0
        try:
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
                prepared.append(
                    {
                        "platform": platform,
                        "account_id": account_id,
                        "account_handle": p.get("account_handle"),
                        "verdict": verdict,
                        "is_seed": is_seed,
                        "stream_ids_json": db.json_dumps([s["stream_id"] for s in passes]),
                    }
                )
            # 先整批写完 validated，再标 completed（避免半截 running）
            for row in prepared:
                db.execute(
                    """
                    INSERT INTO collect_validated_accounts
                      (task_id, platform, account_id, account_handle, verdict, is_seed, stream_ids_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE verdict=VALUES(verdict), stream_ids_json=VALUES(stream_ids_json)
                    """,
                    (
                        task_id,
                        row["platform"],
                        row["account_id"],
                        row["account_handle"],
                        row["verdict"],
                        row["is_seed"],
                        row["stream_ids_json"],
                    ),
                )
            self.set_step_status(
                task_id,
                "step6_validated",
                "completed",
                message=f"已收敛 {count} 个可信账号",
                payload={"validated_count": count, "platforms": platforms_for_posts},
            )
        except Exception as exc:
            logger.exception("run_validated_accounts 失败 task=%s: %s", task_id, exc)
            # 仅未完成时保持 running 以便重试；禁止把已 completed 打回 running
            if _step_status(task_id, "step6_validated") != "completed":
                self.set_step_status(
                    task_id,
                    "step6_validated",
                    "running",
                    message=f"收敛中断将重试：{str(exc)[:120]}",
                )
            return

        # 收口后的附属工作：失败不影响 4.2 终态（4.3 由引擎另推）
        try:
            self.prepare_step7_children_pending(task_id)
        except Exception as exc:
            logger.warning("prepare_step7 after validated 失败 task=%s: %s", task_id, exc)
        for row in prepared:
            try:
                from collect_01.display_store import sync_validated_display

                sync_validated_display(
                    task_id,
                    row["platform"],
                    row["account_id"],
                    step_key="step6_validated",
                )
            except Exception as exc:
                logger.warning("展示层双写 validated 失败: %s", exc)

    def prepare_step7_children_pending(self, task_id: str) -> List[str]:
        """步骤六完成后预建发文子节点，父步骤保持 pending。"""
        rows = db.fetch_all(
            """
            SELECT DISTINCT platform FROM collect_validated_accounts
            WHERE task_id=%s AND verdict='validated'
            """,
            (task_id,),
        )
        platforms = sorted({str(r.get("platform") or "") for r in rows if r.get("platform")})
        if platforms:
            self.ensure_post_steps(task_id, platforms)
        keep_keys = {post_platform_step_key(p) for p in platforms}
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
                self.set_step_status(task_id, step_key, "skipped", message="未纳入可信账号，跳过该平台发文采集")
        cur7 = get_step_status(task_id, "step7_posts")
        if cur7 in {"running"} and not platforms:
            self.set_step_status(task_id, "step7_posts", "pending", message="等待发文采集")
        elif cur7 is None:
            self.ensure_step_row(task_id, "step7_posts")
        return platforms

    def materialize_step7_from_validated(self, task_id: str) -> List[str]:
        """步骤七开始：预建子节点；若已有发文工具则点亮父节点 running。"""
        platforms = self.prepare_step7_children_pending(task_id)
        # 仅在调用方明确进入发文轮时点亮（见 sink 首个发文工具）
        return platforms

    def start_step7_if_ready(self, task_id: str) -> bool:
        """首个发文工具触发时点亮步骤七父节点 running。

        禁止由 pre_llm/系统管线空转调用把父节点抢跑成 running；
        系统侧只应 prepare_step7_children_pending。
        """
        from report_04.gates import can_advance_to_step7

        if not can_advance_to_step7(task_id).get("ok"):
            return False
        self.prepare_step7_children_pending(task_id)
        cur = get_step_status(task_id, "step7_posts")
        if cur in {"pending", None}:
            self.set_step_status(
                task_id,
                "step7_posts",
                "running",
                message="发文采集中",
                force_reopen=True,
            )
            return True
        # 父已收口后：仅当仍有未尝试平台才回开（已齐平台重复催调不回开）
        if cur == "completed":
            try:
                from report_04.gates import list_inflight_post_platforms
                from report_04.step_reconcile import list_unattempted_post_platforms

                leftover = list_unattempted_post_platforms(task_id)
                inflight = list_inflight_post_platforms(task_id)
            except Exception:
                leftover, inflight = [], []
            if not leftover and not inflight:
                return False
            self.set_step_status(
                task_id,
                "step7_posts",
                "running",
                message="发文采集中（晚到补采）",
                force_reopen=True,
            )
            return True
        return cur == "running"

    def save_analysis_display(
        self,
        task_id: str,
        step_key: str,
        content: str,
        *,
        source: str = "standalone",
    ) -> None:
        """写入步骤8/9/10 分析展示。终稿回填对外与 Agent 独立分析一致（不暴露 backfill 来源）。"""
        if not content or not step_key:
            return
        # source 仅兼容调用方参数，展示与步骤 message 一律按 Agent 分析呈现
        _ = source
        body = content
        if step_key == "step9_context_views":
            body = self._enrich_step9_post_evidence(task_id, body)
        try:
            from collect_01.display_store import upsert_display_record

            # 展示层只写正文；不写 source，避免出现 backfill_from_step11
            upsert_display_record(
                task_id=task_id,
                step_key=step_key,
                data_type="report_analysis",
                source_table="hermes_user_dialogues",
                source_ref=f"{step_key}:analysis",
                row={"content": body[:50000]},
                platform=None,
                account_id=None,
            )
        except Exception as exc:
            logger.warning("写 report_analysis display 失败: %s", exc)
        self.set_step_status(
            task_id,
            step_key,
            "completed",
            message="分析完成",
            payload={"length": len(body)},
        )

    def ensure_step8_image_analysis(self, task_id: str) -> bool:
        """步骤8详情读 collect_images；有图且达分析上限/无待办时收口 completed。"""
        if not task_id:
            return False
        try:
            from collect_01 import db
            from report_04.image_assets import (
                _POST_MEDIA_MAX_IMAGES,
                count_image_analysis_stats,
            )

            stats = count_image_analysis_stats(task_id)
            pm = count_image_analysis_stats(task_id, source_type="post_media")
            n = stats["total"]
            analyzed = stats["analyzed"]
            cap = _POST_MEDIA_MAX_IMAGES
        except Exception as exc:
            logger.warning("查询 collect_images 失败 task=%s: %s", task_id, exc)
            return False
        if n <= 0:
            return False

        # 发文配图达上限，或限额内已无待办 → 可 completed（不要求扫完全部发现图）
        quota_done = pm["analyzed"] >= cap
        pm_idle = pm["total"] > 0 and pm["pending_store"] == 0 and pm["pending_analyze"] == 0
        all_done = analyzed >= n
        if not (quota_done or pm_idle or all_done):
            pending = n - analyzed
            target = min(cap, pm["total"]) if pm["total"] > 0 else n
            msg = (
                f"图片流分析中（发文配图已分析 {pm['analyzed']}/{target}"
                f"{'，上限 ' + str(cap) if pm['total'] > cap else ''}）"
            )
            self.set_step_status(
                task_id,
                "step8_img_analysis",
                "running",
                message=msg,
                payload={
                    "image_count": n,
                    "analyzed": analyzed,
                    "pending": pending,
                    "post_media_analyzed": pm["analyzed"],
                    "post_media_total": pm["total"],
                    "post_media_cap": cap,
                },
                touch_updated_at=False,
            )
            return False
        try:
            from collect_01 import db

            # 清除误把「账号基本信息」回填进 step8 的旧展示记录
            db.execute(
                """
                DELETE FROM collect_display_records
                WHERE task_id=%s AND step_key='step8_img_analysis'
                  AND data_type='report_analysis'
                """,
                (task_id,),
            )
        except Exception as exc:
            logger.warning("清理 step8 误回填展示失败 task=%s: %s", task_id, exc)
        st = get_step_status(task_id, "step8_img_analysis")
        if st in {"completed", "skipped"}:
            return True
        if quota_done or (pm["total"] > cap):
            msg = f"图片流分析完成（发文配图已分析 {pm['analyzed']} 张，上限 {cap}）"
        else:
            msg = f"图片流分析完成（已分析 {analyzed}/{n} 张）"
        self.set_step_status(
            task_id,
            "step8_img_analysis",
            "completed",
            message=msg,
            payload={
                "image_count": n,
                "analyzed": analyzed,
                "post_media_analyzed": pm["analyzed"],
                "post_media_total": pm["total"],
                "post_media_cap": cap,
            },
        )
        return True

    def _enrich_step9_post_evidence(self, task_id: str, content: str) -> str:
        """观点块缺作证时，从 collect_posts 补若干条原文摘录（供详情展示）。"""
        from report_04.report_parser import _has_post_evidence

        text = (content or "").strip()
        if _has_post_evidence(text):
            return text
        try:
            from collect_01 import db

            rows = db.fetch_all(
                """
                SELECT platform, account_id, content_text, title, published_at
                FROM collect_posts
                WHERE task_id=%s
                ORDER BY published_at DESC, id DESC
                LIMIT 15
                """,
                (task_id,),
            )
        except Exception as exc:
            logger.warning("补 step9 作证查发文失败 task=%s: %s", task_id, exc)
            return text
        if not rows:
            return text
        citations = []
        for i, row in enumerate(rows, 1):
            line = self._format_post_citation_line(i, row)
            if line:
                citations.append(line)
        if not citations:
            return text

        # 填补「发文作证：」后为空、直接接下一观点的槽位
        slot_pat = re.compile(
            r"(发文作证\s*[：:]\s*\n)(?=\s*(?:###|\Z))",
            re.MULTILINE,
        )
        matches = list(slot_pat.finditer(text))
        if matches:
            post_idx = 0
            parts: List[str] = []
            last = 0
            for m in matches:
                parts.append(text[last : m.end()])
                chunk = []
                for seq in range(1, min(4, len(citations) + 1)):
                    chunk.append(citations[post_idx % len(citations)])
                    post_idx += 1
                parts.append("\n".join(chunk) + "\n")
                last = m.end()
            parts.append(text[last:])
            return "".join(parts)

        lines = [text, "", "发文作证（库内入库原文摘录）："]
        lines.extend(citations[:10])
        return "\n".join(lines)

    def _format_post_citation_line(self, seq: int, row: Dict[str, Any]) -> str:
        plat = str(row.get("platform") or "").strip()
        pub = row.get("published_at")
        date_s = ""
        if pub is not None:
            try:
                date_s = pub.strftime("%Y年%m月%d日")
            except Exception:
                date_s = str(pub)[:10]
        excerpt = str(row.get("content_text") or row.get("title") or "").strip()
        excerpt = excerpt.replace("\n", " ")
        if len(excerpt) > 200:
            excerpt = excerpt[:200] + "…"
        if not excerpt:
            return ""
        if date_s:
            return f"{seq}. {date_s}在{plat}平台发文称「{excerpt}」"
        return f"{seq}. 在{plat}平台发文称「{excerpt}」"

    def finalize_task(
        self,
        task_id: str,
        *,
        session_ended: bool = True,
        fail_reason: Optional[str] = None,
    ) -> None:
        """收口任务。session_ended=True（默认，on_session_end）：stream 结束则流程必须终态，禁止再标 running。

        fail_reason：会话提前结束等场景的明确失败文案（优先于通用「未产出终稿」）。
        """
        from report_04.engine import full_reconcile_enabled, run_session_finalize_light

        task = self.get_task(task_id) or {}
        if str(task.get("status") or "") in {"failed", "cancelled", "completed"}:
            # 已终态：不再 reconcile 改回 running
            return

        early_fail = str(fail_reason or "").strip() or None
        # 未出终稿且续跑中：禁止 session_end 砍步骤 / 标 failed
        defer_continue = False
        if session_ended:
            try:
                from report_04.session_continue import (
                    has_final_report,
                    should_defer_finalize,
                )

                if not has_final_report(task_id) and should_defer_finalize(task_id):
                    defer_continue = True
            except Exception:
                defer_continue = False
        # 会话结束且发文已开放但未尝试：补强失败文案（续跑中不写）
        if session_ended and not early_fail and not defer_continue:
            try:
                from report_04.gates import can_run_step7_collect
                from report_04.step_reconcile import list_unattempted_post_platforms

                if can_run_step7_collect(task_id):
                    todo = list_unattempted_post_platforms(task_id)
                    if todo:
                        plats = ", ".join(
                            str(i.get("platform") or "") for i in todo[:8] if i.get("platform")
                        )
                        early_fail = (
                            f"会话结束：步骤7发文已开放但未调用即结束（未尝试 {len(todo)} 个平台"
                            + (f"：{plats}" if plats else "")
                            + "）"
                        )
            except Exception:
                pass

        if full_reconcile_enabled():
            from report_04.step_reconcile import reconcile_stuck_pipeline

            reconcile_stuck_pipeline(
                self,
                task_id,
                allow_skip_unattempted_step4=bool(session_ended),
            )
            _reconcile_report_post_child_steps(self, task_id)
            self.reconcile_collect_child_steps(task_id)
            from report_04.step_reconcile import close_collect_parent_if_ready
            from report_04.orchestrator import close_open_steps_for_session_end

            close_collect_parent_if_ready(self, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕")
            if session_ended and not defer_continue:
                close_open_steps_for_session_end(
                    self,
                    task_id,
                    reason=early_fail or "会话结束：Agent stream 已结束",
                )
        else:
            if not defer_continue:
                run_session_finalize_light(
                    self,
                    task_id,
                    reason=early_fail or "会话结束：Agent stream 已结束",
                )
            else:
                logger.info(
                    "finalize 跳过 session_finalize_light（续跑中） task=%s",
                    task_id,
                )

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
        step2 = _step_status(task_id, "step2_maigret")
        step5 = _step_status(task_id, "step6_validated")
        step2_ok = step2 in {"completed", "skipped"}
        step5_ok = step5 in {"completed", "skipped"}
        # stream 结束：再兜底一次，确保无 pending/running 步骤残留（续跑中禁止）
        if session_ended and not defer_continue:
            try:
                from report_04.orchestrator import close_open_steps_for_session_end

                close_open_steps_for_session_end(
                    self,
                    task_id,
                    reason=early_fail or "会话结束：Agent stream 已结束",
                )
            except Exception as exc:
                logger.warning("finalize session_end 步骤收口失败 task=%s: %s", task_id, exc)
        post_children = _report_post_step_rows(task_id)
        # 会话未结束：禁止仅因 validated/子节点存在就点亮 step7 running
        # （须等 Agent 首个发文工具 → sink.start_step7_if_ready；否则会抢跑甩开 stream）
        if not session_ended:
            legacy_step6 = _step_status(task_id, "step7_posts")
            if legacy_step6 == "pending" and vc <= 0 and not post_children:
                self.set_step_status(
                    task_id,
                    "step7_posts",
                    "skipped",
                    message="无可信账号，跳过发文采集",
                )
        step7_st = _step_status(task_id, "step7_posts")
        step7_ok = step7_st in {"completed", "skipped", "failed"}
        post_children = _report_post_step_rows(task_id)
        post_children_ok = all(
            str(r.get("status") or "") in {"completed", "skipped", "failed"} for r in post_children
        )
        step11 = _step_status(task_id, "step11_report")
        has_dialogue_summary = db.fetch_one(
            "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
            (task_id,),
        )
        # 分析步已点亮却仍 running：先尝试用对话里的终稿清洗收口
        analysis_running = False
        for ak in ANALYSIS_STEP_KEYS:
            if _step_status(task_id, ak) == "running":
                analysis_running = True
                break
        if not analysis_running and _step_status(task_id, "step11_report") == "running":
            analysis_running = True
        if analysis_running and step11 not in {"completed", "skipped"} and not has_dialogue_summary:
            try:
                from report_04.report_parser import is_final_report, looks_like_report_attempt
                from report_04.sink import _complete_step11_from_report

                dlg = db.fetch_one(
                    """
                    SELECT content FROM hermes_user_dialogues
                    WHERE task_id=%s AND msg_type IN ('thoughts_final', 'assistant_reply')
                    ORDER BY id DESC LIMIT 1
                    """,
                    (task_id,),
                )
                raw = str((dlg or {}).get("content") or "")
                if raw and (is_final_report(raw) or looks_like_report_attempt(raw)):
                    sid = str((self.get_task(task_id) or {}).get("session_id") or "") or None
                    _complete_step11_from_report(self, task_id, raw, sid)
                    step11 = _step_status(task_id, "step11_report")
                    has_dialogue_summary = db.fetch_one(
                        "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
                        (task_id,),
                    )
            except Exception as exc:
                logger.warning("finalize 脏/终稿兜底收口失败 task=%s: %s", task_id, exc)
            # session 结束：仍无终稿则强制 skip 分析/报告空转
            if session_ended:
                from report_04.orchestrator import close_open_steps_for_session_end

                close_open_steps_for_session_end(
                    self,
                    task_id,
                    reason=early_fail or "会话结束：Agent stream 已结束",
                )
                analysis_running = False
                step11 = _step_status(task_id, "step11_report")
                step7_st = _step_status(task_id, "step7_posts")
                step7_ok = step7_st in {"completed", "skipped", "failed"}

        # 终稿已落库 → completed；会话结束则流程必须终态（有终稿 completed，否则 failed）
        if step11 in {"completed", "skipped"} and has_dialogue_summary:
            ready_done = True
        elif session_ended:
            ready_done = False
        else:
            ready_done = step2_ok and step5_ok and step7_ok and (
                poc > 0 or post_children_ok or not post_children
            )
            if analysis_running:
                ready_done = False
                logger.info(
                    "finalize 暂缓 completed：分析/报告步仍 running task=%s",
                    task_id,
                )

        summary_ok = f"写报完成：{vc} 个可信账号，{pc} 条资料，{poc} 条发文"
        summary_partial = (
            f"会话结束收口：step2={step2}，step6={step5}，step7={step7_st}，"
            f"step11={step11}；当前 {pc} 条资料，{poc} 条发文（未产出终稿）"
        )
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
                summary_ok if ready_done else summary_partial,
            ),
        )
        # 发文后第二次图片管线：Hook 内后台线程，不阻塞 finalize
        if ready_done or step7_ok or session_ended:
            try:
                from report_04.image_assets import spawn_second_image_pipeline

                spawn_second_image_pipeline(task_id, store=self)
            except Exception as exc:
                logger.warning("finalize 后台第二次图片管线异常 task=%s: %s", task_id, exc)
        if ready_done:
            db.execute(
                "UPDATE hermes_tasks SET status='completed', current_phase=%s, finished_at=COALESCE(finished_at, NOW(3)), error_message=NULL WHERE task_id=%s AND status NOT IN ('failed', 'cancelled')",
                (PHASE_DONE, task_id),
            )
        elif session_ended:
            if defer_continue:
                db.execute(
                    """
                    UPDATE hermes_tasks
                    SET status='running',
                        finished_at=NULL,
                        error_message=NULL,
                        updated_at=NOW(3)
                    WHERE task_id=%s AND status NOT IN ('failed', 'completed', 'cancelled')
                    """,
                    (task_id,),
                )
                logger.info(
                    "finalize session_ended 暂缓 failed：续跑中保持 running task=%s",
                    task_id,
                )
            else:
                # stream 已结束：流程一并终态，禁止继续 running 空等
                err_msg = early_fail or "会话结束：Agent stream 已结束，流程已收口（未产出终稿）"
                db.execute(
                    """
                    UPDATE hermes_tasks
                    SET status='failed',
                        current_phase=%s,
                        error_message=%s,
                        finished_at=COALESCE(finished_at, NOW(3)),
                        updated_at=NOW(3)
                    WHERE task_id=%s AND status NOT IN ('failed', 'completed', 'cancelled')
                    """,
                    (
                        PHASE_DONE,
                        err_msg[:500],
                        task_id,
                    ),
                )
                logger.info("finalize session_ended → failed task=%s reason=%s", task_id, err_msg[:120])
        else:
            current_phase = PHASE_REPORT if not step5_ok else PHASE_POSTS
            db.execute(
                "UPDATE hermes_tasks SET status='running', current_phase=%s, finished_at=NULL, updated_at=NOW(3) WHERE task_id=%s AND status NOT IN ('failed', 'completed', 'cancelled')",
                (current_phase, task_id),
            )
