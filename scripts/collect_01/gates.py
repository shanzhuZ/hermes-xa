"""01 采集 — 步骤完成门禁（查库，不信模型口头进度）。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from collect_01 import db


def check_step1_seed(task_id: str, platform: str) -> Dict[str, Any]:
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_profiles
        WHERE task_id=%s AND platform=%s
        """,
        (task_id, platform),
    )
    count = int((row or {}).get("c") or 0)
    return {"ok": count >= 1, "message": f"种子平台 {platform} profile 行数={count}"}


def check_step2_maigret(task_id: str, *, skipped: bool) -> Dict[str, Any]:
    if skipped:
        return {"ok": True, "message": "未要求跨平台，step2 已跳过"}
    called = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s AND status='success'
        """,
        (task_id, "mcp_maigret_collect_accounts"),
    )
    n_call = int((called or {}).get("c") or 0)
    cand = db.fetch_one(
        "SELECT COUNT(*) AS c FROM cross_platform_candidates WHERE task_id=%s",
        (task_id,),
    )
    n_cand = int((cand or {}).get("c") or 0)
    ok = n_call >= 1
    return {
        "ok": ok,
        "message": f"Maigret 成功调用={n_call}, 候选={n_cand}",
        "candidates": n_cand,
    }


def assert_gate(result: Dict[str, Any], step_label: str) -> None:
    if not result.get("ok"):
        raise RuntimeError(f"{step_label} gate 未通过: {result.get('message')}")


def get_step_status(task_id: str, step_key: str) -> Optional[str]:
    row = db.fetch_one(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, step_key),
    )
    return str((row or {}).get("status") or "").strip() or None


_STEP4_TOOL_NAMES = (
    "mcp_ocr_perform_ocr",
    "mcp_vision_analyze",
    "vision_analyze",
)


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
    """已处理（含失败）的图片流数量。"""
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
    """每条图片流均已完成 Vision 处理（成功或失败）；无图片流则视为已就绪。"""
    n_img = count_image_streams(task_id)
    if n_img == 0:
        return True
    return count_image_streams_processed(task_id) >= n_img


def _expand_post_child_pending(task_id: str) -> Optional[str]:
    """扩建任务：步骤二下分平台发文子步骤须全部终态（排除遗留 step6_posts）。"""
    task = db.fetch_one(
        "SELECT task_type FROM hermes_tasks WHERE task_id=%s",
        (task_id,),
    )
    if (task or {}).get("task_type") != "account_expand":
        return None
    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key='step3_profiles'
          AND step_key LIKE %s AND step_key <> 'step6_posts'
        """,
        (task_id, "step6_post_%"),
    )
    for row in rows:
        st = str(row.get("status") or "")
        if st in ("pending", "running"):
            return f"{row.get('step_key')}={st}"
    return None


def can_advance_to_step45(task_id: str) -> Dict[str, Any]:
    """step4 文本比对 / step5 收敛前门禁：step1 完成，step2 完成或跳过，step3 完成。"""
    task = db.fetch_one(
        "SELECT cross_platform, task_type FROM hermes_tasks WHERE task_id=%s",
        (task_id,),
    )
    if not task:
        return {"ok": False, "message": "任务不存在"}

    s1 = get_step_status(task_id, "step1_seed")
    if s1 != "completed":
        return {"ok": False, "message": f"step1_seed={s1 or 'pending'}，未采种子 profile"}

    cross = int(task.get("cross_platform") or 0)
    s2 = get_step_status(task_id, "step2_cross_platform")
    if cross:
        if s2 not in ("completed", "skipped"):
            return {"ok": False, "message": f"step2_cross_platform={s2 or 'pending'}，跨平台任务须先完成或跳过 Maigret"}
    elif s2 not in ("completed", "skipped"):
        return {"ok": False, "message": f"step2_cross_platform={s2 or 'pending'}，应标记为 skipped"}

    s3 = get_step_status(task_id, "step3_profiles")
    if s3 != "completed":
        pending_child = _expand_post_child_pending(task_id)
        if pending_child:
            return {"ok": False, "message": f"步骤二发文子步骤未全部完成（{pending_child}）"}
        return {"ok": False, "message": f"step3_profiles={s3 or 'pending'}，候选主页未采完"}

    pending_child = _expand_post_child_pending(task_id)
    if pending_child:
        return {"ok": False, "message": f"步骤二发文子步骤未全部完成（{pending_child}）"}

    return {"ok": True, "message": "满足 step4/5 推进条件"}
