"""04 写报 — 步骤完成门禁。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from collect_01 import db
from report_04.phases import ANALYSIS_STEP_KEYS, TASK_TYPE


def get_step_status(task_id: str, step_key: str) -> Optional[str]:
    row = db.fetch_one(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, step_key),
    )
    return str((row or {}).get("status") or "").strip() or None


def _seed_platform(task_id: str) -> str:
    row = db.fetch_one("SELECT seed_json FROM hermes_tasks WHERE task_id=%s", (task_id,))
    if not row:
        return "twitter"
    try:
        seed = json.loads(row.get("seed_json") or "{}")
    except json.JSONDecodeError:
        return "twitter"
    return str(seed.get("platform") or "twitter")


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


def is_stream_compare_ready(task_id: str) -> bool:
    n = count_image_streams(task_id)
    if n == 0:
        return True
    return count_image_streams_processed(task_id) >= n


def discovery_steps_terminal(task_id: str) -> bool:
    """步骤二、三均已结束（完成或跳过）后，才允许步骤四采集收口。"""
    s2 = get_step_status(task_id, "step2_maigret")
    s3 = get_step_status(task_id, "step3_web_search")
    return s2 in {"completed", "skipped"} and s3 in {"completed", "skipped"}


def can_close_step4_parent(task_id: str) -> bool:
    return discovery_steps_terminal(task_id)


def step7_collect_active(task_id: str) -> bool:
    """步骤七已开始（running/completed/skipped）才允许创建或更新发文子步骤。"""
    return get_step_status(task_id, "step7_posts") in {"running", "completed", "skipped"}


def can_run_step7_collect(task_id: str) -> bool:
    """步骤六完成后才允许执行发文采集。"""
    return get_step_status(task_id, "step6_validated") == "completed"


def can_advance_to_step5(task_id: str) -> Dict[str, Any]:
    s1 = get_step_status(task_id, "step1_seed")
    if s1 != "completed":
        return {"ok": False, "message": f"step1_seed={s1 or 'pending'}"}
    s4 = get_step_status(task_id, "step4_profiles")
    if s4 not in {"completed", "skipped"}:
        return {"ok": False, "message": f"step4_profiles={s4 or 'pending'}"}
    return {"ok": True, "message": "满足步骤五推进条件"}


def can_advance_to_step7(task_id: str) -> Dict[str, Any]:
    gate = can_advance_to_step5(task_id)
    if not gate.get("ok"):
        return gate
    if get_step_status(task_id, "step5_streams") != "completed":
        return {"ok": False, "message": "step5_streams 未完成"}
    if get_step_status(task_id, "step6_validated") != "completed":
        return {"ok": False, "message": "step6_validated 未完成"}
    return {"ok": True, "message": "满足步骤七推进条件"}


def can_advance_to_analysis(task_id: str) -> Dict[str, Any]:
    gate = can_advance_to_step7(task_id)
    if not gate.get("ok"):
        return gate
    if get_step_status(task_id, "step7_posts") not in {"completed", "skipped"}:
        return {"ok": False, "message": "step7_posts 未完成"}
    return {"ok": True, "message": "满足步骤八～十推进条件"}


def analysis_steps_terminal(task_id: str) -> bool:
    for k in ANALYSIS_STEP_KEYS:
        if get_step_status(task_id, k) not in {"completed", "skipped"}:
            return False
    return True


def can_complete_step11(task_id: str) -> Dict[str, Any]:
    if not analysis_steps_terminal(task_id):
        return {"ok": False, "message": "步骤8～10 尚未全部完成或跳过"}
    return {"ok": True, "message": "可完成步骤十一"}
