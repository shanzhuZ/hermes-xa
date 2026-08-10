"""04 写报 — 步骤完成门禁。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from collect_01 import db
from report_04.phases import ANALYSIS_STEP_KEYS, PROFILE_PARENT_STEP_KEY, TASK_TYPE


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


def can_run_step3_web_search(task_id: str) -> bool:
    """步骤二结束后才允许执行步骤三网页检索。"""
    return get_step_status(task_id, "step2_maigret") in {"completed", "skipped"}


def can_close_step4_parent(task_id: str) -> bool:
    return discovery_steps_terminal(task_id)


def can_update_step4_children(task_id: str) -> bool:
    """步骤二、三结束后才允许创建或更新步骤四主页子节点。"""
    return discovery_steps_terminal(task_id)


def can_update_step7_children(task_id: str) -> bool:
    """步骤六结束后才允许创建或更新步骤七发文子节点。"""
    return can_run_step7_collect(task_id)


def step7_collect_active(task_id: str) -> bool:
    """步骤七已开始（running/completed/skipped）才允许创建或更新发文子步骤。"""
    return get_step_status(task_id, "step7_posts") in {"running", "completed", "skipped"}


def can_advance_to_osint(task_id: str) -> Dict[str, Any]:
    """4.1 + 4.2 均完成后才开放 4.3 社工库核验。"""
    if get_step_status(task_id, "step5_streams") != "completed":
        return {"ok": False, "message": "step5_streams 未完成"}
    if get_step_status(task_id, "step6_validated") != "completed":
        return {"ok": False, "message": "step6_validated 未完成"}
    return {"ok": True, "message": "满足社工库核验推进条件"}


def can_run_step7_collect(task_id: str) -> bool:
    """步骤6 + 4.3 终态后才允许执行发文采集。"""
    if get_step_status(task_id, "step6_validated") != "completed":
        return False
    return get_step_status(task_id, "step6_osint_es") in {"completed", "skipped"}


def _step4_profile_children_pending(task_id: str) -> Optional[str]:
    """步骤四仍有 pending/running 子节点时返回其 step_key。"""
    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    for row in rows:
        st = str(row.get("status") or "")
        if st in {"pending", "running"}:
            return str(row.get("step_key") or "")
    return None


def step4_profiles_terminal(task_id: str) -> bool:
    """步骤四父节点与子节点均已结束。"""
    parent = get_step_status(task_id, "step4_profiles")
    if parent not in {"completed", "skipped"}:
        return False
    return _step4_profile_children_pending(task_id) is None


def step4_profile_collect_started(task_id: str) -> bool:
    """是否已真正开始步骤四主页采集。

    只认步骤树状态，不认 hermes_tool_outputs.phase：
    步骤二/三未完时越序 Apify 常被误标 phase=step4_*，若按工具相位判定会
    在步骤3期间误拦 web_search，并把 Agent 提前推进步骤4。
    """
    parent = get_step_status(task_id, "step4_profiles")
    if parent in {"running", "completed", "skipped"}:
        return True
    rows = db.fetch_all(
        """
        SELECT status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    return any(str(r.get("status") or "") == "running" for r in rows)


def seconds_since_last_tool(
    task_id: str,
    *,
    tool_names: Optional[List[str]] = None,
    phase_prefix: Optional[str] = None,
) -> Optional[float]:
    """距最近一次匹配工具成功的秒数；无记录返回 None。"""
    from datetime import datetime

    clauses = ["task_id=%s", "status='success'"]
    params: List[Any] = [task_id]
    if tool_names:
        placeholders = ",".join(["%s"] * len(tool_names))
        clauses.append(f"tool_name IN ({placeholders})")
        params.extend(tool_names)
    if phase_prefix:
        clauses.append("phase LIKE %s")
        params.append(f"{phase_prefix}%")
    row = db.fetch_one(
        f"""
        SELECT MAX(executed_at) AS last_at FROM hermes_tool_outputs
        WHERE {' AND '.join(clauses)}
        """,
        tuple(params),
    )
    last_at = (row or {}).get("last_at")
    if last_at is None:
        return None
    if hasattr(last_at, "timestamp"):
        try:
            return max(0.0, (datetime.now() - last_at).total_seconds())
        except Exception:
            return None
    return None


def can_advance_to_step5(task_id: str) -> Dict[str, Any]:
    s1 = get_step_status(task_id, "step1_seed")
    if s1 != "completed":
        return {"ok": False, "message": f"step1_seed={s1 or 'pending'}"}
    s4 = get_step_status(task_id, "step4_profiles")
    if s4 not in {"completed", "skipped"}:
        return {"ok": False, "message": f"step4_profiles={s4 or 'pending'}"}
    pending_child = _step4_profile_children_pending(task_id)
    if pending_child:
        return {"ok": False, "message": f"步骤四子步骤未完成（{pending_child}）"}
    return {"ok": True, "message": "满足步骤五推进条件"}


def can_advance_to_step7(task_id: str) -> Dict[str, Any]:
    gate = can_advance_to_step5(task_id)
    if not gate.get("ok"):
        return gate
    if get_step_status(task_id, "step5_streams") != "completed":
        return {"ok": False, "message": "step5_streams 未完成"}
    if get_step_status(task_id, "step6_validated") != "completed":
        return {"ok": False, "message": "step6_validated 未完成"}
    osint = get_step_status(task_id, "step6_osint_es")
    if osint not in {"completed", "skipped"}:
        return {"ok": False, "message": f"step6_osint_es={osint or 'pending'}"}
    return {"ok": True, "message": "满足步骤七推进条件"}


def _platform_post_count(task_id: str, platform: str) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s AND platform=%s",
        (task_id, platform),
    )
    return int((row or {}).get("c") or 0)


def posts_substantively_ready(task_id: str) -> bool:
    """发文实质已齐：可进分析（不要求 step7_posts 父壳 UI 已 completed）。

    条件：步骤7门禁可过、无未尝试发文平台、现有 step7_post_* 子步均为 completed/skipped
    （视频分析走 step7_video_* 旁路，不挡此判定与进分析；
    但 step7_posts / phase_content 壳收口另等视频终态）。
    """
    if not can_advance_to_step7(task_id).get("ok"):
        return False
    try:
        from report_04.step_reconcile import list_unattempted_post_platforms

        if list_unattempted_post_platforms(task_id):
            return False
    except Exception:
        return False
    from report_04.phases import POST_PARENT_STEP_KEY

    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, POST_PARENT_STEP_KEY),
    )
    if rows:
        for r in rows:
            st = str(r.get("status") or "")
            if st in {"completed", "skipped"}:
                continue
            # running 但已有入库：视为实质完成（兼容旧「等视频」钉 running）
            if st == "running":
                sk = str(r.get("step_key") or "")
                plat = sk.replace("step7_post_", "", 1) if sk.startswith("step7_post_") else ""
                if plat and _platform_post_count(task_id, plat) > 0:
                    continue
            return False
        return True
    # 无子节点：父已终态或已有发文
    if get_step_status(task_id, "step7_posts") in {"completed", "skipped"}:
        return True
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s", (task_id,)
    )
    return int((row or {}).get("c") or 0) > 0


def can_advance_to_analysis(task_id: str) -> Dict[str, Any]:
    gate = can_advance_to_step7(task_id)
    if not gate.get("ok"):
        return gate
    # 仍有未尝试发文的 validated：禁止进分析（逼 Agent 先调工具）
    try:
        from report_04.step_reconcile import list_unattempted_post_platforms

        leftover = list_unattempted_post_platforms(task_id)
        if leftover:
            plats = ",".join(str(x.get("platform") or "") for x in leftover[:8])
            return {
                "ok": False,
                "message": f"仍有未尝试发文平台：{plats}",
            }
    except Exception:
        pass
    # 父壳 completed 或发文实质已齐均可进分析
    if get_step_status(task_id, "step7_posts") in {"completed", "skipped"}:
        return {"ok": True, "message": "满足步骤八～十推进条件"}
    if posts_substantively_ready(task_id):
        return {"ok": True, "message": "发文实质已齐，可进步骤八～十"}
    return {"ok": False, "message": "step7_posts 未完成（发文未齐）"}


def analysis_steps_terminal(task_id: str) -> bool:
    for k in ANALYSIS_STEP_KEYS:
        if get_step_status(task_id, k) not in {"completed", "skipped"}:
            return False
    return True


def can_complete_step11(task_id: str) -> Dict[str, Any]:
    if not analysis_steps_terminal(task_id):
        return {"ok": False, "message": "步骤8～10 尚未全部完成或跳过"}
    return {"ok": True, "message": "可完成步骤十一"}
