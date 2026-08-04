"""03 账号核查任务与步骤状态写入。"""

from __future__ import annotations

import json
import logging
import hashlib
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from collect_01 import db
from verify_03.phases import (
    PHASE_ACCOUNT_FINALIZE,
    PHASE_CROSS_PLATFORM,
    PHASE_DONE,
    PHASE_INPUT_ACCOUNTS,
    PHASE_STREAM_GEN,
    PHASE_STREAM_VALIDATE,
    PLATFORM_LABELS,
    PROFILE_PARENT_STEP_KEY,
    ROOT_STEPS,
    TASK_TYPE,
    initial_steps,
    is_profile_platform_step,
    is_post_platform_step,
    post_platform_step_key,
    post_step_node,
    post_step_order,
    post_step_title,
    profile_platform_step_key,
    profile_step_node,
    profile_step_order,
    profile_step_title,
    step_phase,
)

from common.source_tag import source_tag_json

logger = logging.getLogger(__name__)

_VERIFY_INTENT = re.compile(
    r"(account-intelligence-verification|account-intelligence-verify|账号核查|核查.*?(推特|twitter|微博|weibo|facebook|youtube|instagram))",
    re.I,
)


def _now_sql() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def is_verify_intent(user_message: str) -> bool:
    return bool(_VERIFY_INTENT.search(user_message or ""))


def is_three_section_report(content: str) -> bool:
    """判断是否为核查三节终稿。"""
    text = (content or "").replace(" ", "").replace("\u3000", "")
    if len(text) < 30:
        return False
    markers = ("一、账号基础信息", "二、各个平台账号发言", "三、账号核验结果")
    return sum(1 for m in markers if m in text) >= 2


def extract_three_section_body(content: str) -> Optional[str]:
    """从混杂进度文中截取三节终稿正文（从「一、账号基础信息」起）。"""
    text = (content or "").strip()
    if not is_three_section_report(text):
        return None
    for marker in (
        "一、账号基础信息",
        "## 一、账号基础信息",
        "**一、账号基础信息**",
    ):
        idx = text.find(marker)
        if idx >= 0:
            return text[idx:].strip()
    return text


def _stream_id_base(task_id: str, platform: str, account_id: str) -> str:
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


def _load_seed(task_id: str) -> Dict[str, Any]:
    row = db.fetch_one("SELECT seed_json FROM hermes_tasks WHERE task_id=%s", (task_id,))
    try:
        return json.loads((row or {}).get("seed_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _save_seed(task_id: str, seed: Dict[str, Any]) -> None:
    db.execute(
        "UPDATE hermes_tasks SET seed_json=%s, updated_at=NOW(3) WHERE task_id=%s",
        (db.json_dumps(seed), task_id),
    )


class TaskStore:
    """03 账号核查入库门面。"""

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

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return db.fetch_one("SELECT * FROM hermes_tasks WHERE task_id=%s", (task_id,))

    def has_user_dialogue(self, task_id: str, msg_type: str = "user_input") -> bool:
        row = db.fetch_one(
            "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type=%s LIMIT 1",
            (task_id, msg_type),
        )
        return bool(row)

    def bind_session(self, task_id: str, session_id: str) -> None:
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

    def ensure_task(
        self,
        *,
        session_id: str,
        user_message: str,
        task_id: Optional[str] = None,
    ) -> Optional[str]:
        if not is_verify_intent(user_message):
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
        seed = {"input_accounts": [], "parse_status": "pending"}
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO hermes_tasks
                      (task_id, task_type, session_id, status, current_phase, cross_platform, seed_json, started_at)
                    VALUES (%s, %s, %s, 'running', %s, 1, %s, NOW(3))
                    """,
                    (new_id, TASK_TYPE, session_id or None, PHASE_INPUT_ACCOUNTS, db.json_dumps(seed)),
                )
                cur.execute(
                    """
                    INSERT INTO hermes_user_dialogues (task_id, session_id, role, content, msg_type)
                    VALUES (%s, %s, 'user', %s, 'user_input')
                    """,
                    (new_id, session_id, user_message[:65535]),
                )
                for step in initial_steps():
                    status = "running" if step.step_key == "step1_input_accounts" else "pending"
                    cur.execute(
                        """
                        INSERT IGNORE INTO collect_phase_steps
                          (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            new_id,
                            step.step_key,
                            step.parent_step_key,
                            step.step_order,
                            step.step_node,
                            step.title,
                            source_tag_json(step.step_key, streams_as_self=True),
                            status,
                        ),
                    )
        if _step_status(new_id, "step1_input_accounts") != "running":
            self.set_step_status(new_id, "step1_input_accounts", "running", message="等待 Agent 确认种子账号…")
        logger.info("创建核查任务 task_id=%s session=%s", new_id, session_id)
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

    def get_input_accounts(self, task_id: str) -> List[Dict[str, Any]]:
        seed = _load_seed(task_id)
        accounts = seed.get("input_accounts")
        return accounts if isinstance(accounts, list) else []

    def ensure_profile_platform_steps(self, task_id: str, accounts: List[Dict[str, Any]]) -> None:
        """步骤一完成后，在 step3_profiles 下按平台预建「主页 + 发文」子步骤。"""
        for acc in accounts:
            plat = str(acc.get("platform") or "").strip()
            if not plat:
                continue
            handle = str(acc.get("account_handle") or acc.get("account_id") or "").strip()
            label = PLATFORM_LABELS.get(plat, plat)
            handle_label = f" · @{handle.lstrip('@')}" if handle else ""
            prof_key = profile_platform_step_key(plat)
            post_key = post_platform_step_key(plat)
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    prof_key,
                    PROFILE_PARENT_STEP_KEY,
                    profile_step_order(plat),
                    profile_step_node(plat),
                    f"{profile_step_title(plat)}{handle_label}",
                    source_tag_json(prof_key),
                ),
            )
            db.execute(
                """
                INSERT IGNORE INTO collect_phase_steps
                  (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (
                    task_id,
                    post_key,
                    PROFILE_PARENT_STEP_KEY,
                    post_step_order(plat),
                    post_step_node(plat),
                    f"{post_step_title(plat)}{handle_label}",
                    source_tag_json(post_key),
                ),
            )

    def save_input_accounts(
        self,
        task_id: str,
        accounts: List[Dict[str, Any]],
        *,
        source: str = "agent",
    ) -> None:
        if not accounts:
            return
        seed = _load_seed(task_id)
        seed["input_accounts"] = accounts
        seed["parse_status"] = "completed"
        _save_seed(task_id, seed)
        self._sync_input_accounts_display(task_id, accounts)
        self.ensure_profile_platform_steps(task_id, accounts)
        src_label = "Agent 确认" if source == "agent" else "用户输入解析"
        self.set_step_status(
            task_id,
            "step1_input_accounts",
            "completed",
            message=f"已确认 {len(accounts)} 个待核查账号（{src_label}）",
            payload={"accounts": accounts, "parse_source": source},
        )
        if _step_status(task_id, "step3_profiles") == "pending":
            self.set_step_status(task_id, "step3_profiles", "running", message="各平台主页与发文采集中…")

    def update_tool_output_phase(self, tool_output_id: int, step_key: str) -> None:
        """将工具调用记录的 phase 修正为 collect_phase_steps.step_key。"""
        if not step_key:
            return
        db.execute(
            "UPDATE hermes_tool_outputs SET phase=%s WHERE id=%s",
            (step_key, tool_output_id),
        )

    def reconcile_profile_platform_steps(self, task_id: str) -> int:
        """根据库表事实校正子步骤，并在全部终态后收口 step3_profiles。"""
        from verify_03.step_reconcile import (
            auto_skip_unattempted_collect_children,
            reconcile_step3_collect_child_steps,
        )

        # 两轮：先收口「等待发文」空结果，再 peer-skip 未开跑平台，避免互相死锁
        updated = 0
        for _ in range(2):
            updated += int(auto_skip_unattempted_collect_children(self, task_id, aggressive=False) or 0)
            updated += int(reconcile_step3_collect_child_steps(self, task_id) or 0)

        rows = db.fetch_all(
            """
            SELECT step_key, status FROM collect_phase_steps
            WHERE task_id=%s AND parent_step_key=%s
            """,
            (task_id, PROFILE_PARENT_STEP_KEY),
        )
        if not rows:
            return updated
        for row in rows:
            st = str(row.get("status") or "pending")
            if st in {"pending", "running"}:
                return updated
        if _step_status(task_id, "step3_profiles") != "completed":
            self.set_step_status(
                task_id,
                "step3_profiles",
                "completed",
                message="各平台主页与发文采集已尝试完毕",
            )
            updated += 1
            # 步骤二刚收口：仅后台 kickoff，禁止同步等待（否则 post_tool Hook 120s 超时）
            try:
                from verify_03.image_assets import kickoff_images_background

                kickoff_images_background(task_id, reason="step3_profiles_completed")
            except Exception as exc:
                logger.warning("step3 收口后图片入库 kickoff 失败 task=%s: %s", task_id, exc)
        # 仅当步骤二已完成后才点亮步骤三，禁止抢跑
        if (
            _step_status(task_id, "step3_profiles") == "completed"
            and _step_status(task_id, "step3_streams") == "pending"
        ):
            self.set_step_status(
                task_id,
                "step3_streams",
                "running",
                message="待发博文风格与领域归纳…",
            )
        return updated

    def close_unattempted_platforms_and_reconcile(
        self, task_id: str, *, aggressive: bool = False
    ) -> int:
        """
        校正/尝试收口平台子步骤。
        aggressive=True 仅会话结束硬收口；采集中途（含 Vision）必须 False，
        否则会在 Agent 尚未对某种子调 MCP 时误 skip（如 YouTube 先 web_search 查 channelId）。
        """
        from verify_03.step_reconcile import auto_skip_unattempted_collect_children

        n = auto_skip_unattempted_collect_children(self, task_id, aggressive=aggressive)
        n += self.reconcile_profile_platform_steps(task_id)
        return n

    def _sync_input_accounts_display(self, task_id: str, accounts: List[Dict[str, Any]]) -> None:
        from collect_01.display_store import upsert_display_record
        from verify_03.phases import PLATFORM_LABELS

        for idx, acc in enumerate(accounts):
            plat = str(acc.get("platform") or "")
            handle = str(acc.get("account_handle") or "")
            label = PLATFORM_LABELS.get(plat, plat)
            row = {
                "platform": plat,
                "account_handle": handle,
                "display_label": acc.get("display_label") or label,
            }
            upsert_display_record(
                task_id=task_id,
                step_key="step1_input_accounts",
                data_type="input_accounts",
                source_table="hermes_tasks",
                source_ref=f"seed:{idx}:{plat}:{handle}",
                row=row,
                platform=plat,
                account_id=handle or acc.get("account_id"),
            )

    def set_task_phase(self, task_id: str, phase: str) -> None:
        db.execute(
            "UPDATE hermes_tasks SET current_phase=%s, updated_at=NOW(3) WHERE task_id=%s",
            (phase, task_id),
        )

    def ensure_step_row(self, task_id: str, step_key: str) -> None:
        for step in ROOT_STEPS:
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
                        source_tag_json(step.step_key, streams_as_self=True),
                    ),
                )
                return
        if is_profile_platform_step(step_key):
            platform = step_key.replace("step3_profile_", "", 1)
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
            return
        if is_post_platform_step(step_key):
            platform = step_key.replace("step3_post_", "", 1)
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
                    post_step_order(platform),
                    post_step_node(platform),
                    post_step_title(platform),
                    source_tag_json(step_key),
                ),
            )
            return

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

    def save_post_rows(self, rows: List[Dict[str, Any]], *, step_key: str = "step3_profiles") -> None:
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

    def _match_pending_stream_by_url(
        self, task_id: str, tool_args: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """vision URL 变体未精确匹配时，走统一图片流匹配。"""
        return _find_image_stream_for_tool(task_id, tool_args)

    def mark_image_stream_progress(
        self,
        task_id: str,
        tool_name: str,
        tool_args: Dict[str, Any],
        *,
        success: bool,
    ) -> bool:
        if tool_name not in {"mcp_ocr_perform_ocr", "mcp_vision_analyze", "vision_analyze"}:
            return False
        stream = _find_image_stream_for_tool(task_id, tool_args)
        if not stream and tool_name in {"mcp_vision_analyze", "vision_analyze"}:
            stream = self._match_pending_stream_by_url(task_id, tool_args)
        if not stream:
            return False
        sid = str(stream.get("stream_id") or "")
        if not sid:
            return False
        if tool_name == "mcp_ocr_perform_ocr":
            if success:
                db.execute(
                    """
                    UPDATE collect_identity_streams
                    SET validation_detail=%s, updated_at=NOW(3)
                    WHERE stream_id=%s AND validation_status='pending'
                    """,
                    ("OCR 完成，等待 vision", sid),
                )
            return True
        status = "pass" if success else "fail"
        detail = "Vision 分析完成" if success else "Vision 分析失败"
        if not success:
            source = _extract_image_source(tool_args)
            if source.startswith(("http://", "https://")):
                db.execute(
                    """
                    UPDATE collect_identity_streams
                    SET validation_detail=%s, updated_at=NOW(3)
                    WHERE stream_id=%s AND validation_status='pending'
                    """,
                    ("vision 远程 URL 失败，等待本地重试", sid),
                )
                return True
        db.execute(
            """
            UPDATE collect_identity_streams
            SET validation_status=%s, validation_detail=%s, updated_at=NOW(3)
            WHERE stream_id=%s
            """,
            (status, detail, sid),
        )
        try:
            from collect_01.display_store import sync_stream_display

            sync_stream_display(sid, step_key="step4_image_compare")
        except Exception as exc:
            logger.warning("展示层双写 image stream 失败: %s", exc)
        return True

    def mark_remaining_image_streams_failed(self, task_id: str, detail: str) -> int:
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

    def save_style_analysis(self, task_id: str, content: str) -> None:
        """步骤三：风格归纳原文写入 payload。须等步骤二（主页+发文）收口后再 completed。"""
        preview = content[:8000]
        if _step_status(task_id, "step3_profiles") != "completed":
            # 禁止步骤二未完成就点亮/完成步骤三（父晚子早 / 步骤三抢跑）
            self.reconcile_profile_platform_steps(task_id)
            if _step_status(task_id, "step3_profiles") != "completed":
                logger.info("风格归纳暂缓：step3_profiles 未完成 task=%s", task_id)
                return
        cur = _step_status(task_id, "step3_streams")
        if cur == "pending":
            self.set_step_status(task_id, "step3_streams", "running", message="发文风格与领域归纳中…")
        self.set_step_status(
            task_id,
            "step3_streams",
            "completed",
            message="发文风格与领域归纳完成",
            payload={"analysis_preview": preview},
        )

    def run_text_compare(self, task_id: str) -> None:
        """全种子互比：文本流规则比对。须等风格归纳（step3_streams）完成。"""
        from verify_03.gates import can_advance_to_step45

        gate = can_advance_to_step45(task_id)
        if not gate.get("ok"):
            logger.info("run_text_compare 跳过 task=%s: %s", task_id, gate.get("message"))
            return
        if _step_status(task_id, "step4_text_compare") != "completed":
            self.set_step_status(task_id, "step4_text_compare", "running", message="文本流规则比对中")

        profiles = db.fetch_all(
            "SELECT platform, account_id, account_handle, collect_status FROM collect_profiles WHERE task_id=%s",
            (task_id,),
        )
        existing = [p for p in profiles if str(p.get("collect_status") or "") in {"success", "partial"}]
        all_text = db.fetch_all(
            "SELECT * FROM collect_identity_streams WHERE task_id=%s AND stream_type='text'",
            (task_id,),
        )
        if len(existing) < 2 or not all_text:
            self.set_step_status(
                task_id,
                "step4_text_compare",
                "completed",
                message=f"可比对账号不足（有效 profile {len(existing)}）",
            )
            return

        # 按账号聚合文本
        by_acct: Dict[str, str] = {}
        for row in all_text:
            key = f"{row.get('source_platform')}:{row.get('source_account_id')}"
            by_acct[key] = (by_acct.get(key, "") + " " + str(row.get("payload_text") or "")).strip().lower()

        matched = 0
        for row in all_text:
            plat = row.get("source_platform") or ""
            acct = row.get("source_account_id") or ""
            field = row.get("source_field") or ""
            text = (row.get("payload_text") or "").lower().strip()
            status = "fail"
            if not text:
                status = "insufficient"
            else:
                self_key = f"{plat}:{acct}"
                for other_key, other_blob in by_acct.items():
                    if other_key == self_key:
                        continue
                    if field == "account_handle":
                        other_handles = [
                            (r.get("payload_text") or "").lower().strip().lstrip("@")
                            for r in all_text
                            if r.get("source_field") == "account_handle"
                            and f"{r.get('source_platform')}:{r.get('source_account_id')}" == other_key
                        ]
                        if text.lstrip("@") in other_handles:
                            status = "pass"
                            break
                    elif field in ("display_name", "bio") and len(text) >= 2:
                        if text in other_blob or other_blob in text:
                            status = "pass"
                            break
            if status == "pass":
                matched += 1
            db.execute(
                "UPDATE collect_identity_streams SET validation_status=%s, validation_detail=%s WHERE stream_id=%s",
                (status, "核查文本流互比", row["stream_id"]),
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
            message=f"文本流互比完成，匹配 {matched}/{len(all_text)} 条",
            payload={"matched": matched, "total": len(all_text)},
        )

    def _pair_verdict(self, plat_a: str, acct_a: str, plat_b: str, acct_b: str, task_id: str) -> Dict[str, Any]:
        if plat_a == plat_b and acct_a.lower() == acct_b.lower():
            return {"verdict": "same_account", "confidence": 1.0}
        streams_a = db.fetch_all(
            """
            SELECT source_field, validation_status, payload_text FROM collect_identity_streams
            WHERE task_id=%s AND source_platform=%s AND source_account_id=%s AND stream_type='text'
            """,
            (task_id, plat_a, acct_a),
        )
        streams_b = db.fetch_all(
            """
            SELECT source_field, validation_status, payload_text FROM collect_identity_streams
            WHERE task_id=%s AND source_platform=%s AND source_account_id=%s AND stream_type='text'
            """,
            (task_id, plat_b, acct_b),
        )
        handle_a = next((s for s in streams_a if s.get("source_field") == "account_handle"), None)
        handle_b = next((s for s in streams_b if s.get("source_field") == "account_handle"), None)
        ha = (handle_a or {}).get("payload_text", "").lower().lstrip("@")
        hb = (handle_b or {}).get("payload_text", "").lower().lstrip("@")
        if ha and hb and ha == hb:
            return {"verdict": "same_person", "confidence": 0.95, "reason": "handle 完全一致"}
        name_a = next((s for s in streams_a if s.get("source_field") == "display_name"), None)
        name_b = next((s for s in streams_b if s.get("source_field") == "display_name"), None)
        na = (name_a or {}).get("payload_text", "").lower().strip()
        nb = (name_b or {}).get("payload_text", "").lower().strip()
        if na and nb and na == nb:
            return {"verdict": "same_person", "confidence": 0.9, "reason": "显示名称一致"}
        passes = sum(1 for s in streams_a if s.get("validation_status") == "pass")
        if passes >= 2:
            return {"verdict": "possible_match", "confidence": 0.6, "reason": "部分文本流相似"}
        return {"verdict": "unrelated", "confidence": 0.0, "reason": "无明显关联"}

    def run_validated_accounts(self, task_id: str) -> None:
        """步骤五：全种子互比结果写入 collect_validated_accounts。"""
        seed = _load_seed(task_id)
        input_accounts = seed.get("input_accounts") or []
        profiles = db.fetch_all(
            "SELECT platform, account_id, account_handle, collect_status FROM collect_profiles WHERE task_id=%s",
            (task_id,),
        )
        profile_map = {
            (str(p["platform"]), str(p.get("account_handle") or p["account_id"]).lower()): p for p in profiles
        }
        count = 0
        for acc in input_accounts:
            plat = str(acc.get("platform") or "")
            handle = str(acc.get("account_handle") or acc.get("account_id") or "")
            prof = profile_map.get((plat, handle.lower()))
            exists = prof is not None and str(prof.get("collect_status") or "") in {"success", "partial"}
            acct_id = str((prof or {}).get("account_id") or handle)
            post_cnt = db.fetch_one(
                """
                SELECT COUNT(*) AS c FROM collect_posts
                WHERE task_id=%s AND platform=%s AND LOWER(account_id)=LOWER(%s)
                """,
                (task_id, plat, acct_id),
            )
            has_posts = int((post_cnt or {}).get("c") or 0) > 0
            pairs: List[Dict[str, Any]] = []
            for other in input_accounts:
                o_plat = str(other.get("platform") or "")
                o_handle = str(other.get("account_handle") or other.get("account_id") or "")
                if plat == o_plat and handle.lower() == o_handle.lower():
                    continue
                o_prof = profile_map.get((o_plat, o_handle.lower()))
                o_exists = o_prof is not None and str(o_prof.get("collect_status") or "") in {"success", "partial"}
                if not exists or not o_exists:
                    pairs.append(
                        {
                            "peer_platform": o_plat,
                            "peer_account_handle": o_handle,
                            "verdict": "not_found" if not o_exists else "insufficient",
                            "confidence": None,
                        }
                    )
                    continue
                pv = self._pair_verdict(plat, handle, o_plat, o_handle, task_id)
                pairs.append(
                    {
                        "peer_platform": o_plat,
                        "peer_account_handle": o_handle,
                        "verdict": pv.get("verdict"),
                        "confidence": pv.get("confidence"),
                        "reason": pv.get("reason"),
                    }
                )
            verdict = "validated" if exists and has_posts else ("insufficient" if exists else "rejected")
            if exists:
                count += 1
            account_id = (prof or {}).get("account_id") or handle
            extra = {
                "is_input_seed": True,
                "account_exists": exists,
                "has_posts": has_posts,
                "verification_pairs": pairs,
            }
            db.execute(
                """
                INSERT INTO collect_validated_accounts
                  (task_id, platform, account_id, account_handle, verdict, is_seed, extra_json)
                VALUES (%s,%s,%s,%s,%s,1,%s)
                ON DUPLICATE KEY UPDATE
                  verdict=VALUES(verdict), account_handle=VALUES(account_handle), extra_json=VALUES(extra_json)
                """,
                (task_id, plat, account_id, handle, verdict, db.json_dumps(extra)),
            )
            try:
                from collect_01.display_store import sync_validated_display

                sync_validated_display(task_id, plat, account_id, step_key="step5_validated")
            except Exception as exc:
                logger.warning("展示层双写 validated 失败: %s", exc)
        self.set_step_status(
            task_id,
            "step5_validated",
            "completed",
            message=f"账号核验完成，有效账号 {count}/{len(input_accounts)}",
            payload={"validated_count": count, "input_count": len(input_accounts)},
        )

    def save_assistant_output(self, task_id: str, session_id: Optional[str], content: str) -> bool:
        text = (content or "").strip()
        if not text or not task_id or text == "(empty)":
            return False
        if is_three_section_report(text):
            from verify_03.gates import is_vision_gate_ready

            vision_gate = is_vision_gate_ready(task_id)
            if not vision_gate.get("ok"):
                # 不落 summary、不收口任务：逼模型先调 vision_analyze
                logger.warning(
                    "终稿被 Vision 门禁拦截 task=%s: %s",
                    task_id,
                    vision_gate.get("message"),
                )
                clipped_reply = text[:65535]
                dup = db.fetch_one(
                    """
                    SELECT id FROM hermes_user_dialogues
                    WHERE task_id=%s AND role='assistant' AND msg_type=%s AND content=%s
                    LIMIT 1
                    """,
                    (task_id, "assistant_reply", clipped_reply),
                )
                if not dup:
                    self.save_dialogue(
                        task_id, session_id, "assistant", text, "assistant_reply"
                    )
                return False

            body = extract_three_section_body(text) or text
            clipped = body[:65535]
            # 闸门：终稿入库前必须先尝试图片资产（禁止报告后再补跑）
            try:
                from verify_03.image_assets import ensure_images_before_report

                ensure_images_before_report(task_id, reason="before_summary")
            except Exception as exc:
                logger.warning("终稿前图片入库失败 task=%s: %s", task_id, exc)
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
                logger.info("已更新核查终稿 summary task=%s len=%d", task_id, len(body))
            else:
                self.save_dialogue(task_id, session_id, "assistant", body, "summary")
                logger.info("已保存核查终稿 summary task=%s len=%d", task_id, len(body))
            # 终稿已出：立即收口步骤树，避免前端卡在 step3_streams
            try:
                self.close_analysis_after_report(task_id)
            except Exception as exc:
                logger.warning("终稿后步骤收口失败 task=%s: %s", task_id, exc)
            return True
        clipped = text[:65535]
        dup = db.fetch_one(
            """
            SELECT id FROM hermes_user_dialogues
            WHERE task_id=%s AND role='assistant' AND msg_type=%s AND content=%s
            LIMIT 1
            """,
            (task_id, "assistant_reply", clipped),
        )
        if dup:
            return False
        self.save_dialogue(task_id, session_id, "assistant", text, "assistant_reply")
        return True

    def close_analysis_after_report(self, task_id: str) -> None:
        """终稿已出时收口步骤2采集子树 + step3_streams / step4 / step5，并把任务标 completed。"""
        from verify_03.gates import (
            count_image_streams,
            is_image_compare_ready,
            is_vision_gate_ready,
        )
        from verify_03.step_reconcile import (
            auto_skip_unattempted_collect_children,
            force_close_open_collect_children,
            reconcile_step3_collect_child_steps,
        )

        # 终稿已出：步骤2必须终态（禁止任务 completed 但 2.x.1 仍 running）
        try:
            reconcile_step3_collect_child_steps(self, task_id)
            auto_skip_unattempted_collect_children(self, task_id, aggressive=True)
            force_close_open_collect_children(self, task_id)
            self.reconcile_profile_platform_steps(task_id)
            if _step_status(task_id, "step3_profiles") in {"pending", "running"}:
                self.set_step_status(
                    task_id,
                    "step3_profiles",
                    "completed",
                    message="终稿已出，主页与发文采集收口",
                )
        except Exception as exc:
            logger.warning("终稿收口步骤2失败 task=%s: %s", task_id, exc)

        if _step_status(task_id, "step3_profiles") in {"completed", "skipped"}:
            if _step_status(task_id, "step3_streams") in {"pending", "running"}:
                self.set_step_status(
                    task_id,
                    "step3_streams",
                    "completed",
                    message="终稿已出，风格归纳收口",
                )

        if _step_status(task_id, "step3_streams") == "completed":
            if _step_status(task_id, "step4_text_compare") in {"pending", "running"}:
                try:
                    self.run_text_compare(task_id)
                except Exception as exc:
                    logger.warning("终稿收口 text_compare 失败 task=%s: %s", task_id, exc)
                if _step_status(task_id, "step4_text_compare") in {"pending", "running"}:
                    self.set_step_status(
                        task_id,
                        "step4_text_compare",
                        "completed",
                        message="终稿已出，文本比对收口",
                    )

            vision_ok = bool(is_vision_gate_ready(task_id).get("ok"))
            if _step_status(task_id, "step4_image_compare") in {"pending", "running"}:
                n_img = count_image_streams(task_id)
                if n_img == 0:
                    self.set_step_status(
                        task_id,
                        "step4_image_compare",
                        "completed",
                        message="无头像图片流，跳过图片比对",
                    )
                elif vision_ok or is_image_compare_ready(task_id):
                    # 仅当已真实做过 Vision（或流已非 pending）才收口；禁止「未调 vision 假 fail」
                    if vision_ok:
                        self.set_step_status(
                            task_id,
                            "step4_image_compare",
                            "completed",
                            message="图片流 Vision 完成",
                        )
                        try:
                            from verify_03.image_assets import kickoff_images_after_vision

                            kickoff_images_after_vision(task_id, reason="close_analysis_step4")
                        except Exception as exc:
                            logger.warning("终稿收口后图片分析 kickoff 失败 task=%s: %s", task_id, exc)
                    else:
                        logger.warning(
                            "终稿收口暂缓图片流：Vision 门禁未过 task=%s",
                            task_id,
                        )
                else:
                    logger.warning(
                        "终稿收口暂缓图片流：仍有未 Vision 的头像 task=%s",
                        task_id,
                    )

            if (
                _step_status(task_id, "step4_text_compare") == "completed"
                and _step_status(task_id, "step4_image_compare") == "completed"
                and _step_status(task_id, "step5_validated") in {"pending", "running"}
            ):
                try:
                    self.run_validated_accounts(task_id)
                except Exception as exc:
                    logger.warning("终稿收口 validated 失败 task=%s: %s", task_id, exc)
                if _step_status(task_id, "step5_validated") in {"pending", "running"}:
                    self.set_step_status(
                        task_id,
                        "step5_validated",
                        "completed",
                        message="终稿已出，核验收口",
                    )

        # 仍有未终态分析节点则强制收口（图片流除外：无 Vision 不得假完成）
        for step_key, message in (
            ("step3_streams", "终稿已出，风格归纳收口"),
            ("step4_text_compare", "终稿已出，文本比对收口"),
            ("step5_validated", "终稿已出，核验收口"),
        ):
            if _step_status(task_id, step_key) in {"pending", "running"}:
                self.set_step_status(task_id, step_key, "completed", message=message)
        if (
            _step_status(task_id, "step4_image_compare") in {"pending", "running"}
            and is_vision_gate_ready(task_id).get("ok")
        ):
            self.set_step_status(
                task_id,
                "step4_image_compare",
                "completed",
                message="终稿已出，图片比对收口",
            )

        if _step_status(task_id, "step5_validated") == "completed":
            db.execute(
                """
                UPDATE hermes_tasks
                SET status='completed', current_phase=%s, finished_at=COALESCE(finished_at, NOW(3)), updated_at=NOW(3)
                WHERE task_id=%s AND status IN ('pending', 'running')
                """,
                (PHASE_DONE, task_id),
            )
            # 步骤2事后收口可能把 current_phase 改回 cross_platform，校正为 done
            db.execute(
                """
                UPDATE hermes_tasks
                SET current_phase=%s, updated_at=NOW(3)
                WHERE task_id=%s AND status='completed' AND IFNULL(current_phase,'')<>%s
                """,
                (PHASE_DONE, task_id, PHASE_DONE),
            )

    def finalize_task(self, task_id: str) -> None:
        from verify_03.step_reconcile import reconcile_stuck_pipeline

        reconcile_stuck_pipeline(self, task_id)

        # 仅当尚无终稿时兜底：已有 summary 说明报告已出，禁止再「报告后补图」
        summary = db.fetch_one(
            "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
            (task_id,),
        )
        if not summary:
            try:
                step3 = _step_status(task_id, "step3_profiles")
                pc = int(
                    (db.fetch_one("SELECT COUNT(*) AS c FROM collect_profiles WHERE task_id=%s", (task_id,)) or {}).get(
                        "c"
                    )
                    or 0
                )
                poc = int(
                    (db.fetch_one("SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s", (task_id,)) or {}).get("c")
                    or 0
                )
                if step3 in {"completed", "skipped"} or pc > 0 or poc > 0:
                    from verify_03.image_assets import ensure_images_before_report

                    ensure_images_before_report(task_id, reason="finalize_before_summary")
            except Exception as exc:
                logger.warning("finalize 图片资产兜底异常 task=%s: %s", task_id, exc)

        # 有终稿则强制收口步骤树（解决「报告已出但卡在步骤3」）
        if summary:
            try:
                self.close_analysis_after_report(task_id)
            except Exception as exc:
                logger.warning("finalize 终稿收口失败 task=%s: %s", task_id, exc)

        step5 = _step_status(task_id, "step5_validated")
        ready_done = bool(summary) and step5 == "completed"
        if ready_done:
            db.execute(
                """
                UPDATE hermes_tasks
                SET status='completed', current_phase=%s, finished_at=COALESCE(finished_at, NOW(3)), updated_at=NOW(3)
                WHERE task_id=%s AND status IN ('pending', 'running')
                """,
                (PHASE_DONE, task_id),
            )
        else:
            db.execute(
                "UPDATE hermes_tasks SET updated_at=NOW(3) WHERE task_id=%s",
                (task_id,),
            )
