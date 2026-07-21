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
    TASK_TYPE,
    PLATFORM_LABELS,
    initial_steps,
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
            store.set_step_status(
                task_id,
                step_key,
                "completed",
                message=f"已采集 {platform} 发文 {cnt} 条",
                payload={"post_count": cnt},
            )
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
            elif platform == seed_plat:
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"种子平台 {platform} 未在步骤七尝试发文采集",
                )
                updated += 1
            elif platform in validated:
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"{platform} 已纳入可信账号，但未尝试发文采集",
                )
                updated += 1
            continue
        if platform in validated or platform == seed_plat:
            store.set_step_status(
                task_id,
                step_key,
                "skipped",
                message=f"{platform} 已纳入可信账号，但未采集到发文",
            )
            updated += 1
    # 子节点收口后立刻关父节点，避免「子全终态、父仍 running」
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
                          (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                        VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (task_id, step.step_key, step.parent_step_key, step.step_order, step.step_node, step.title),
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
                          (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                        VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                        """,
                        (new_id, step.step_key, step.parent_step_key, step.step_order, step.step_node, step.title),
                    )
        self.set_step_status(new_id, "step1_seed", "running", message="等待种子 profile 采集…")
        self.set_step_status(new_id, "step2_maigret", "running", message="等待 Maigret 跨平台发现…")
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
        is_report = is_final_report(text)
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
            ensure_step4_parent_not_premature,
            ensure_step5_not_premature,
            ensure_step7_parent_not_premature,
            maybe_close_abandoned_step4,
            reconcile_step4_and_step7_children,
        )

        # 先尝试收口「已开干又被 Agent 扔下」的步骤四，避免父节点永久 running
        updated = maybe_close_abandoned_step4(self, task_id)
        updated += reconcile_step4_and_step7_children(self, task_id)
        updated += ensure_step4_parent_not_premature(self, task_id)
        updated += ensure_step7_parent_not_premature(self, task_id)
        for parent, msg_done in (
            (PROFILE_PARENT_STEP_KEY, "候选主页采集已尝试完毕"),
            (POST_PARENT_STEP_KEY, "发文采集已尝试完毕"),
        ):
            updated += close_collect_parent_if_ready(self, task_id, parent, msg_done)
        # 步骤四仍未终态时，回滚 Java 粗同步误点的步骤五
        updated += ensure_step5_not_premature(self, task_id)
        return updated

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

    def ensure_profile_steps(self, task_id: str, platforms: List[str]) -> None:
        parent = PROFILE_PARENT_STEP_KEY
        for platform in sorted(set(platforms), key=profile_step_order):
            key = profile_platform_step_key(platform)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    key,
                    parent,
                    profile_step_order(platform),
                    profile_step_node(platform),
                    profile_step_title(platform),
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
        # 跳过无采集通道 / 与种子无关的 Maigret 或 web 噪声子节点
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
                    message=f"{plat} 候选与种子账号不匹配，跳过",
                )
            elif plat in relevant_set and cur == "skipped":
                # 曾被误 skip 的可采集平台：恢复 pending，避免父步骤提前收口后晚到 Apify 把步骤树打乱
                self.set_step_status(
                    task_id,
                    sk,
                    "pending",
                    message=f"{plat} 待主页采集",
                )
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
        touch_updated_at: bool = True,
        force_reopen: bool = False,
    ) -> None:
        self.ensure_step_row(task_id, step_key)
        current = db.fetch_one(
            "SELECT status, finished_at, payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
            (task_id, step_key),
        )
        cur_status = str((current or {}).get("status") or "")

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

        fields = ["status=%s"]
        params: List[Any] = [status]
        if touch_updated_at:
            fields.append("updated_at=NOW(3)")
        if status == "running":
            fields.append("started_at=COALESCE(started_at, NOW(3))")
            # 子步骤允许 skipped→running（补采）；父步骤已在上面拦截（除非 force）
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
        phase = step_phase(step_key)
        # 仅 running/completed 推进 current_phase；pending 回滚不改 phase（避免步骤7误抢 phase=posts）
        if phase and status in {"running", "completed"}:
            self.set_task_phase(task_id, phase)

    def ensure_step_row(self, task_id: str, step_key: str) -> None:
        """旧任务可能缺少 step4_profiles 等新步骤行。"""
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
        if step_key.startswith("step7_post_"):
            platform = step_key.replace("step7_post_", "", 1)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                """,
                (task_id, step_key, POST_PARENT_STEP_KEY, post_step_order(platform), post_step_node(platform), post_step_title(platform)),
            )
            return
        if step_key.startswith("step4_profile_"):
            platform = step_key.replace("step4_profile_", "", 1)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    step_key,
                    PROFILE_PARENT_STEP_KEY,
                    profile_step_order(platform),
                    profile_step_node(platform),
                    profile_step_title(platform),
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
        except Exception as exc:
            logger.warning("展示层双写 image streams 失败: %s", exc)
        return n

    def _step5_payload(self, task_id: str) -> Dict[str, Any]:
        row = db.fetch_one(
            "SELECT payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key='step5_streams'",
            (task_id,),
        )
        try:
            payload = json.loads((row or {}).get("payload_json") or "{}")
        except Exception:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def run_stream_validation(self, task_id: str) -> None:
        """步骤五：文本/图片流与种子比对。"""
        from report_04.gates import can_advance_to_step5, count_image_streams, is_stream_compare_ready

        gate = can_advance_to_step5(task_id)
        if not gate.get("ok"):
            logger.info("run_stream_validation 跳过 task=%s: %s", task_id, gate.get("message"))
            return
        task = self.get_task(task_id)
        if not task:
            return

        existing = self._step5_payload(task_id)
        # 文本已比对且仍等图片：禁止反复整段重跑（会闪 message + 清超时计时）
        if existing.get("text_compare_done") and _step_status(task_id, "step5_streams") == "running":
            n_img = count_image_streams(task_id)
            if is_stream_compare_ready(task_id):
                matched = int(existing.get("matched") or 0)
                total = int(existing.get("total") or 0)
                self.set_step_status(
                    task_id,
                    "step5_streams",
                    "completed",
                    message=(
                        f"图片流 Vision 完成 ({n_img}/{n_img})"
                        if n_img > 0
                        else f"文本流比对完成，通过 {matched}/{total} 条"
                    ),
                    payload={
                        "matched": matched,
                        "total": total,
                        "text_compare_done": True,
                        "image_pending": 0,
                    },
                )
                self.run_validated_accounts(task_id)
                return
            matched = int(existing.get("matched") or 0)
            total = int(existing.get("total") or 0)
            wait_since = existing.get("wait_images_since") or datetime.now().isoformat(timespec="seconds")
            self.set_step_status(
                task_id,
                "step5_streams",
                "running",
                message=f"文本流比对完成（通过 {matched}/{total}）；等待图片流 OCR/Vision",
                payload={
                    "matched": matched,
                    "total": total,
                    "image_pending": n_img,
                    "text_compare_done": True,
                    "wait_images_since": wait_since,
                },
                touch_updated_at=False,
            )
            return

        if _step_status(task_id, "step5_streams") != "completed":
            self.set_step_status(task_id, "step5_streams", "running", message="文本流规则比对中")
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
                "step5_streams",
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

            sync_streams_for_task(task_id, step_key="step5_streams", stream_type="text")
        except Exception as exc:
            logger.warning("展示层双写 text streams 失败: %s", exc)

        n_img = count_image_streams(task_id)
        # 仍有待处理图片流时不得提前 completed，等 OCR/Vision（Skill 步骤5 硬要求）
        if n_img > 0 and not is_stream_compare_ready(task_id):
            wait_since = existing.get("wait_images_since") or datetime.now().isoformat(timespec="seconds")
            self.set_step_status(
                task_id,
                "step5_streams",
                "running",
                message=f"文本流比对完成（通过 {matched}/{len(all_text)}）；等待图片流 OCR/Vision",
                payload={
                    "matched": matched,
                    "total": len(all_text),
                    "image_pending": n_img,
                    "text_compare_done": True,
                    "wait_images_since": wait_since,
                },
            )
            self.set_task_phase(task_id, PHASE_STREAM_VALIDATE)
            return

        if n_img > 0 and is_stream_compare_ready(task_id):
            self.set_step_status(
                task_id,
                "step5_streams",
                "completed",
                message=f"图片流 Vision 完成 ({n_img}/{n_img})",
                payload={
                    "matched": matched,
                    "total": len(all_text),
                    "text_compare_done": True,
                    "image_pending": 0,
                },
            )
            self.set_task_phase(task_id, PHASE_STREAM_VALIDATE)
            self.run_validated_accounts(task_id)
            return

        self.set_step_status(
            task_id,
            "step5_streams",
            "completed",
            message=f"文本流比对完成，通过 {matched}/{len(all_text)} 条",
            payload={
                "matched": matched,
                "total": len(all_text),
                "text_compare_done": True,
                "image_pending": 0,
            },
        )

    def kickoff_step5_if_ready(self, task_id: str) -> bool:
        """步骤四收口后立刻启动步骤五：先做文本流规则比对；有待处理图片流则保持 running 等 OCR/Vision。"""
        from report_04.gates import can_advance_to_step5, is_stream_compare_ready

        gate = can_advance_to_step5(task_id)
        if not gate.get("ok"):
            return False
        cur5 = _step_status(task_id, "step5_streams")
        if cur5 not in {"completed", "skipped"}:
            payload = self._step5_payload(task_id)
            if payload.get("text_compare_done"):
                # 已比对过：只刷新等待态，禁止在此 completed→级联步骤6/7
                if is_stream_compare_ready(task_id):
                    self.run_stream_validation(task_id)
            else:
                self.run_stream_validation(task_id)
        # 步骤6/7 由 sink 在 vision settle 后推进，禁止 kickoff 抢跑
        return True

    def run_validated_accounts(self, task_id: str) -> None:
        """步骤六：一次算完并写入可信清单（可重入；展示双写不挡收口）。"""
        if _step_status(task_id, "step5_streams") not in {"completed", "skipped"}:
            logger.info("run_validated_accounts 跳过 task=%s: step5_streams 未完成", task_id)
            return
        if _step_status(task_id, "step6_validated") == "completed":
            return
        self.set_step_status(task_id, "step6_validated", "running", message="收敛可信账号…")
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
            platforms_for_posts: List[str] = []
            count = 0
            prepared: List[Dict[str, Any]] = []
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
            self.prepare_step7_children_pending(task_id)
            # 展示双写放收口之后，失败不影响步骤6终态
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
        except Exception as exc:
            logger.exception("run_validated_accounts 失败 task=%s: %s", task_id, exc)
            # 保持 running 以便后续 post_llm/session_end 重试，勿半截 completed
            self.set_step_status(
                task_id,
                "step6_validated",
                "running",
                message=f"收敛中断将重试：{str(exc)[:120]}",
            )

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
        """步骤六完成后，由首个发文工具点亮步骤七。"""
        from report_04.gates import can_advance_to_step7

        if not can_advance_to_step7(task_id).get("ok"):
            return False
        self.prepare_step7_children_pending(task_id)
        if get_step_status(task_id, "step7_posts") in {"pending", None}:
            self.set_step_status(task_id, "step7_posts", "running", message="发文采集中")
            return True
        return get_step_status(task_id, "step7_posts") == "running"

    def save_analysis_display(
        self,
        task_id: str,
        step_key: str,
        content: str,
        *,
        source: str = "standalone",
    ) -> None:
        if not content or not step_key:
            return
        try:
            from collect_01.display_store import upsert_display_record

            upsert_display_record(
                task_id=task_id,
                step_key=step_key,
                data_type="report_analysis",
                source_table="hermes_user_dialogues",
                source_ref=f"{step_key}:{source}",
                row={"content": content[:50000], "source": source},
                platform=None,
                account_id=None,
            )
        except Exception as exc:
            logger.warning("写 report_analysis display 失败: %s", exc)
        self.set_step_status(
            task_id,
            step_key,
            "completed",
            message="分析完成" if source == "standalone" else "由终稿回填",
            payload={"source": source, "length": len(content)},
        )

    def finalize_task(self, task_id: str) -> None:
        from report_04.engine import full_reconcile_enabled, run_session_finalize_light

        task = self.get_task(task_id) or {}
        if str(task.get("status") or "") == "failed":
            # 种子失败等硬失败终态：不再 reconcile 改回 running
            return

        if full_reconcile_enabled():
            from report_04.step_reconcile import reconcile_stuck_pipeline

            reconcile_stuck_pipeline(self, task_id)
            _reconcile_report_post_child_steps(self, task_id)
            self.reconcile_collect_child_steps(task_id)
            from report_04.step_reconcile import close_collect_parent_if_ready, ensure_step7_parent_active

            ensure_step7_parent_active(self, task_id)
            close_collect_parent_if_ready(self, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕")
        else:
            run_session_finalize_light(self, task_id)

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
        post_children = _report_post_step_rows(task_id)
        post_children_ok = all(str(r.get("status") or "") in {"completed", "skipped", "failed"} for r in post_children)
        legacy_step6 = _step_status(task_id, "step7_posts")
        if legacy_step6 == "pending":
            if vc > 0 or post_children:
                self.set_step_status(task_id, "step7_posts", "running", message="发文采集中")
            else:
                self.set_step_status(
                    task_id,
                    "step7_posts",
                    "skipped",
                    message="无可信账号，跳过发文采集",
                )
        step7_st = _step_status(task_id, "step7_posts")
        step7_ok = step7_st in {"completed", "skipped"}
        step11 = _step_status(task_id, "step11_report")
        has_dialogue_summary = db.fetch_one(
            "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
            (task_id,),
        )
        # 终稿已落库则任务可 completed（避免 step11 完成却 ready_done=false 永久 running）
        if step11 in {"completed", "skipped"} and has_dialogue_summary:
            ready_done = True
        else:
            # 禁止：已有部分发文就标任务 completed，却留下 step7_posts=running
            ready_done = step2_ok and step5_ok and step7_ok and (
                poc > 0 or post_children_ok or not post_children
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
                (
                    f"写报完成：{vc} 个可信账号，{pc} 条资料，{poc} 条发文"
                    if ready_done
                    else f"写报未收口：step2={step2}，step6={step5}，发文子步骤未结束，当前 {pc} 条资料，{poc} 条发文"
                ),
            ),
        )
        # 图片资产兜底：Agent 在 7→8 已跑则跳过；漏跑则补一次（失败不拖垮任务）
        if ready_done or step7_ok:
            try:
                from report_04.image_assets import run_image_pipeline_for_report

                run_image_pipeline_for_report(
                    task_id,
                    force_analyze=True,
                    skip_if_stored=True,
                )
            except Exception as exc:
                logger.warning("finalize 图片资产兜底异常 task=%s: %s", task_id, exc)
        if ready_done:
            db.execute(
                "UPDATE hermes_tasks SET status='completed', current_phase=%s, finished_at=COALESCE(finished_at, NOW(3)) WHERE task_id=%s AND status NOT IN ('failed')",
                (PHASE_DONE, task_id),
            )
        else:
            current_phase = PHASE_REPORT if not step5_ok else PHASE_POSTS
            db.execute(
                "UPDATE hermes_tasks SET status='running', current_phase=%s, finished_at=NULL, updated_at=NOW(3) WHERE task_id=%s AND status NOT IN ('failed', 'completed')",
                (current_phase, task_id),
            )
