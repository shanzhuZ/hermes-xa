"""03 账号核查 — 步骤完成门禁。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from collect_01 import db
from verify_03.phases import TASK_TYPE


def get_step_status(task_id: str, step_key: str) -> Optional[str]:
    row = db.fetch_one(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, step_key),
    )
    return str((row or {}).get("status") or "").strip() or None


def _load_input_accounts(task_id: str) -> List[Dict[str, Any]]:
    row = db.fetch_one("SELECT seed_json FROM hermes_tasks WHERE task_id=%s", (task_id,))
    if not row:
        return []
    try:
        seed = json.loads(row.get("seed_json") or "{}")
    except json.JSONDecodeError:
        return []
    accounts = seed.get("input_accounts")
    return accounts if isinstance(accounts, list) else []


def count_image_streams(task_id: str) -> int:
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image'
        """,
        (task_id,),
    )
    return int((row or {}).get("c") or 0)


def count_image_streams_processed(task_id: str) -> int:
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image'
          AND validation_status IN ('processed', 'pass', 'fail')
        """,
        (task_id,),
    )
    return int((row or {}).get("c") or 0)


def is_image_compare_ready(task_id: str) -> bool:
    n_img = count_image_streams(task_id)
    if n_img == 0:
        return True
    return count_image_streams_processed(task_id) >= n_img


def can_advance_to_step4(task_id: str) -> Dict[str, Any]:
    """文本/图片流分析前：步骤1完成且步骤3（主页+发文）完成。"""
    s1 = get_step_status(task_id, "step1_input_accounts")
    if s1 != "completed":
        return {"ok": False, "message": f"step1_input_accounts={s1 or 'pending'}"}
    s3 = get_step_status(task_id, "step3_profiles")
    if s3 != "completed":
        return {"ok": False, "message": f"step3_profiles={s3 or 'pending'}"}
    return {"ok": True, "message": "满足流分析推进条件"}


def can_advance_to_step45(task_id: str) -> Dict[str, Any]:
    """与 01 命名对齐：step4 对比前须 step3_streams 完成。"""
    gate = can_advance_to_step4(task_id)
    if not gate.get("ok"):
        return gate
    s_stream = get_step_status(task_id, "step3_streams")
    if s_stream != "completed":
        return {"ok": False, "message": f"step3_streams={s_stream or 'pending'}"}
    return {"ok": True, "message": "满足 step4 推进条件"}


def profile_attempt_done(task_id: str, platform: str, handle: str) -> bool:
    """该输入账号是否已有 profile 采集尝试（成功或明确空）。"""
    handle_l = handle.lower().lstrip("@")
    row = db.fetch_one(
        """
        SELECT id, collect_status FROM collect_profiles
        WHERE task_id=%s AND platform=%s
          AND (LOWER(account_handle)=LOWER(%s) OR LOWER(account_id)=LOWER(%s))
        LIMIT 1
        """,
        (task_id, platform, handle_l, handle_l),
    )
    if row:
        return True
    # 工具失败也可能无 profile 行：查工具调用
    tool_row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND status IN ('success', 'error')
          AND phase='step3_profiles'
          AND (tool_args LIKE %s OR tool_args LIKE %s)
        """,
        (task_id, f"%{handle_l}%", f"%{platform}%"),
    )
    return int((tool_row or {}).get("c") or 0) > 0


def all_input_profiles_attempted(task_id: str) -> bool:
    accounts = _load_input_accounts(task_id)
    if not accounts:
        return False
    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key='step3_profiles'
        """,
        (task_id,),
    )
    if rows:
        for row in rows:
            st = str(row.get("status") or "pending")
            if st in {"pending", "running"}:
                return False
        return True
    for acc in accounts:
        plat = str(acc.get("platform") or "")
        handle = str(acc.get("account_handle") or acc.get("account_id") or "")
        if not profile_attempt_done(task_id, plat, handle):
            return False
    return True
