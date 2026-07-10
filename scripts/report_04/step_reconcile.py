"""04 写报 — 步骤收口与卡死恢复。"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

from collect_01 import db
from collect_01.normalizers.apify import APIFY_TOOL_PLATFORM
from report_04.gates import (
    analysis_steps_terminal,
    can_advance_to_analysis,
    can_close_step4_parent,
    can_complete_step11,
    can_advance_to_step7,
    discovery_steps_terminal,
    get_step_status,
    is_stream_compare_ready,
    step7_collect_active,
)
from report_04.phases import (
    POST_PARENT_STEP_KEY,
    PROFILE_PARENT_STEP_KEY,
    is_collectible_platform,
    profile_platform_step_key,
    post_platform_step_key,
)

logger = logging.getLogger(__name__)

_PLATFORM_BY_APIFY_ACTOR: Dict[str, str] = {v: k for k, v in APIFY_TOOL_PLATFORM.items()}


def _post_counts(task_id: str) -> Dict[str, int]:
    rows = db.fetch_all(
        "SELECT platform, COUNT(*) AS c FROM collect_posts WHERE task_id=%s GROUP BY platform",
        (task_id,),
    )
    return {str(r["platform"]): int(r["c"]) for r in rows}


def _prof_counts(task_id: str) -> Dict[str, int]:
    rows = db.fetch_all(
        "SELECT platform, COUNT(*) AS c FROM collect_profiles WHERE task_id=%s GROUP BY platform",
        (task_id,),
    )
    return {str(r["platform"]): int(r["c"]) for r in rows}


def _apify_actor_success(task_id: str, platform: str) -> bool:
    actor_tool = _PLATFORM_BY_APIFY_ACTOR.get(platform) or ""
    if not actor_tool:
        return False
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s AND status='success'
        """,
        (task_id, actor_tool),
    )
    return int((row or {}).get("c") or 0) > 0


def _dataset_success_for_profile(task_id: str, platform: str) -> bool:
    import json as _json
    from collect_01.normalizers.apify import resolve_apify_platform_hint

    prof_key = profile_platform_step_key(platform)
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items'
          AND status='success' AND phase=%s
        """,
        (task_id, prof_key),
    )
    if int((row or {}).get("c") or 0) > 0:
        return True
    actor_tool = _PLATFORM_BY_APIFY_ACTOR.get(platform) or ""
    expected = actor_tool.replace("mcp_apify_", "")
    if not expected:
        return False
    rows = db.fetch_all(
        """
        SELECT id, tool_args, phase FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items' AND status='success'
        """,
        (task_id,),
    )
    for r in rows:
        if str(r.get("phase") or "") != prof_key:
            continue
        args = _json.loads(r.get("tool_args") or "{}")
        ds = str(args.get("datasetId") or args.get("dataset_id") or "").strip()
        hint = resolve_apify_platform_hint(task_id, r["id"], dataset_id=ds or None)
        if hint == expected:
            return True
    return False


def _dataset_success_for_post(task_id: str, platform: str) -> bool:
    """步骤七：仅统计 phase=step7_post_* 的 dataset 拉取。"""
    import json as _json
    from collect_01.normalizers.apify import resolve_apify_platform_hint

    post_key = post_platform_step_key(platform)
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items'
          AND status='success' AND phase=%s
        """,
        (task_id, post_key),
    )
    if int((row or {}).get("c") or 0) > 0:
        return True
    actor_tool = _PLATFORM_BY_APIFY_ACTOR.get(platform) or ""
    expected = actor_tool.replace("mcp_apify_", "")
    if not expected:
        return False
    rows = db.fetch_all(
        """
        SELECT id, tool_args, phase FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items' AND status='success'
        """,
        (task_id,),
    )
    for r in rows:
        if str(r.get("phase") or "") != post_key:
            continue
        args = _json.loads(r.get("tool_args") or "{}")
        ds = str(args.get("datasetId") or args.get("dataset_id") or "").strip()
        hint = resolve_apify_platform_hint(task_id, r["id"], dataset_id=ds or None)
        if hint == expected:
            return True
    return False


def _dataset_success_for_platform(task_id: str, platform: str) -> bool:
    """兼容旧调用：步骤七发文 dataset 检测。"""
    return _dataset_success_for_post(task_id, platform)


def reconcile_step4_and_step7_children(store: Any, task_id: str) -> int:
    updated = 0
    discovery_done = discovery_steps_terminal(task_id)
    post_counts = _post_counts(task_id)
    prof_counts = _prof_counts(task_id)
    children = db.fetch_all(
        """
        SELECT step_key, status, parent_step_key FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key IN (%s, %s)
        """,
        (task_id, PROFILE_PARENT_STEP_KEY, POST_PARENT_STEP_KEY),
    )
    for row in children:
        step_key = str(row.get("step_key") or "")
        cur = str(row.get("status") or "pending")
        if step_key.startswith("step4_profile_"):
            if not discovery_done:
                continue
            plat = step_key.replace("step4_profile_", "", 1)
            cnt = prof_counts.get(plat, 0)
            if cnt > 0 and cur != "completed":
                store.set_step_status(task_id, step_key, "completed", message=f"已入库主页 {cnt} 条")
                updated += 1
                continue
            if cur in {"completed", "skipped", "failed"}:
                continue
            if cur == "pending" and not is_collectible_platform(plat):
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"{plat} 无可用主页采集工具",
                )
                updated += 1
                continue
            if _apify_actor_success(task_id, plat):
                if _dataset_success_for_profile(task_id, plat):
                    if cur != "skipped":
                        store.set_step_status(
                            task_id,
                            step_key,
                            "skipped",
                            message=f"{plat} Apify 已拉取 dataset 但未入库主页",
                        )
                        updated += 1
                elif cur == "pending":
                    store.set_step_status(
                        task_id,
                        step_key,
                        "running",
                        message=f"{plat} Actor 已完成，等待拉取 dataset…",
                    )
                    updated += 1
            continue
        if not step_key.startswith("step7_post_"):
            continue
        if not step7_collect_active(task_id):
            if cur == "skipped" and get_step_status(task_id, "step7_posts") == "pending":
                store.set_step_status(task_id, step_key, "pending", message=None)
                updated += 1
            continue
        plat = step_key.replace("step7_post_", "", 1)
        cnt = post_counts.get(plat, 0)
        if cnt > 0 and cur != "completed":
            store.set_step_status(task_id, step_key, "completed", message=f"已入库发文 {cnt} 条")
            updated += 1
        elif (
            cnt == 0
            and cur in {"running", "pending"}
            and _dataset_success_for_post(task_id, plat)
            and cur != "skipped"
        ):
            store.set_step_status(task_id, step_key, "skipped", message=f"{plat} 未采集到发文")
            updated += 1
    return updated


def _reconcile_vision_from_tools(store: Any, task_id: str) -> int:
    rows = db.fetch_all(
        """
        SELECT tool_name, tool_args FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name IN ('vision_analyze', 'mcp_vision_analyze')
          AND status='success'
        ORDER BY id
        """,
        (task_id,),
    )
    n = 0
    for row in rows:
        try:
            args = json.loads(row.get("tool_args") or "{}")
        except json.JSONDecodeError:
            args = {}
        if store.mark_image_stream_progress(task_id, str(row["tool_name"]), args, success=True):
            n += 1
    return n


def ensure_step4_parent_not_premature(store: Any, task_id: str) -> int:
    """纠正步骤四在步骤二、三完成前被误标 running/completed 的情况。"""
    parent = PROFILE_PARENT_STEP_KEY
    cur = get_step_status(task_id, parent)
    if not discovery_steps_terminal(task_id):
        if cur in {"running", "completed"}:
            store.set_step_status(task_id, parent, "pending", message="等待步骤二、三完成后再采集候选主页")
            return 1
        return 0
    if cur not in {"completed", "skipped"}:
        return 0
    rows = db.fetch_all(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, parent),
    )
    has_active_child = any(str(r.get("status") or "") in {"pending", "running"} for r in rows)
    if has_active_child and cur == "completed":
        store.set_step_status(task_id, parent, "running", message="候选主页采集中")
        return 1
    return 0


def ensure_step7_parent_not_premature(store: Any, task_id: str) -> int:
    """步骤六完成前不得将步骤七标为 running/completed。"""
    parent = POST_PARENT_STEP_KEY
    if can_advance_to_step7(task_id).get("ok"):
        return 0
    cur = get_step_status(task_id, parent)
    updated = 0
    if cur in {"running", "completed"}:
        store.set_step_status(task_id, parent, "pending", message="等待步骤五、六完成")
        updated += 1
    rows = db.fetch_all(
        "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, parent),
    )
    for row in rows:
        step_key = str(row.get("step_key") or "")
        st = str(row.get("status") or "")
        if st in {"running", "completed", "skipped"}:
            store.set_step_status(task_id, step_key, "pending", message=None)
            updated += 1
    return updated


def close_collect_parent_if_ready(store: Any, task_id: str, parent: str, msg_done: str) -> int:
    if parent == PROFILE_PARENT_STEP_KEY:
        ensure_step4_parent_not_premature(store, task_id)
        if not can_close_step4_parent(task_id):
            return 0
    if parent == POST_PARENT_STEP_KEY:
        ensure_step7_parent_not_premature(store, task_id)
        if not can_advance_to_step7(task_id).get("ok"):
            return 0
    rows = db.fetch_all(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, parent),
    )
    if rows:
        if any(str(r.get("status") or "") in {"pending", "running"} for r in rows):
            return 0
    elif get_step_status(task_id, parent) in {"pending", "running"}:
        return 0
    if get_step_status(task_id, parent) not in {"completed", "skipped"}:
        store.set_step_status(task_id, parent, "completed", message=msg_done)
        return 1
    return 0


def reconcile_stuck_pipeline(store: Any, task_id: str) -> None:
    reconcile_step4_and_step7_children(store, task_id)
    _reconcile_vision_from_tools(store, task_id)

    if can_advance_to_step7(task_id).get("ok"):
        store.materialize_step7_from_validated(task_id)

    for parent, msg_done in (
        ("step4_profiles", "候选主页采集已尝试完毕"),
        ("step7_posts", "发文采集已尝试完毕"),
    ):
        close_collect_parent_if_ready(store, task_id, parent, msg_done)

    if can_advance_to_analysis(task_id).get("ok"):
        if get_step_status(task_id, "step5_streams") != "completed":
            if is_stream_compare_ready(task_id):
                store.set_step_status(task_id, "step5_streams", "completed", message="流核查完成")
            else:
                store.run_stream_validation(task_id)

    if analysis_steps_terminal(task_id) and can_complete_step11(task_id).get("ok"):
        if get_step_status(task_id, "step11_report") != "completed":
            summary = db.fetch_one(
                "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
                (task_id,),
            )
            if summary:
                store.set_step_status(task_id, "step11_report", "completed", message="画像报告已生成")
