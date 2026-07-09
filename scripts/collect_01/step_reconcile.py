"""步骤状态收尾：根据库表事实校正 collect_phase_steps。"""

from __future__ import annotations

from typing import Dict, Set

from collect_01 import db
from collect_01.gates import can_advance_to_step45, count_image_streams, get_step_status, is_image_compare_ready
from collect_01.phases import post_step_key

POST_TOOL_BY_PLATFORM: Dict[str, str] = {
    "twitter": "mcp_twitter_get_user_tweets",
    "youtube": "mcp_youtube_analyze_channel_videos",
    "weibo": "mcp_weibo_get_feeds",
}


def _post_counts_by_platform(task_id: str) -> Dict[str, int]:
    rows = db.fetch_all(
        "SELECT platform, COUNT(*) AS c FROM collect_posts WHERE task_id=%s GROUP BY platform",
        (task_id,),
    )
    return {str(r["platform"]): int(r["c"]) for r in rows}


def _validated_platforms(task_id: str) -> Set[str]:
    rows = db.fetch_all(
        """
        SELECT DISTINCT platform FROM collect_validated_accounts
        WHERE task_id=%s AND verdict='validated'
        """,
        (task_id,),
    )
    return {str(r["platform"]) for r in rows}


def _post_tool_success(task_id: str, platform: str) -> bool:
    tool = POST_TOOL_BY_PLATFORM.get(platform)
    if not tool:
        return False
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s AND status='success'
        """,
        (task_id, tool),
    )
    return int((row or {}).get("c") or 0) > 0


def _maigret_success_count(task_id: str) -> int:
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s AND status='success'
        """,
        (task_id, "mcp_maigret_collect_accounts"),
    )
    return int((row or {}).get("c") or 0)


def reconcile_post_child_steps(store, task_id: str) -> int:
    """按 collect_posts 与可信账号清单收口 step6_post_* 子步骤。"""
    post_counts = _post_counts_by_platform(task_id)
    validated = _validated_platforms(task_id)
    expected = validated | set(post_counts.keys())
    if expected:
        store.ensure_post_steps(task_id, sorted(expected))

    children = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key='step6_posts'
        """,
        (task_id,),
    )
    updated = 0
    for row in children:
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
            if _post_tool_success(task_id, platform):
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"{platform} 发文工具已调用但未入库",
                )
            else:
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"可信账号未采集 {platform} 发文",
                )
            updated += 1
    return updated


def reconcile_step6_parent(store, task_id: str, poc: int) -> None:
    """父节点 step6_posts：子步骤全部终态后再标 completed。"""
    children = db.fetch_all(
        """
        SELECT status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key='step6_posts'
        """,
        (task_id,),
    )
    if not children:
        if poc > 0:
            store.set_step_status(
                task_id,
                "step6_posts",
                "completed",
                message=f"分平台发文采集结束，共 {poc} 条",
            )
        else:
            store.set_step_status(
                task_id,
                "step6_posts",
                "pending",
                message="未采集到发文，步骤六未完成",
            )
        return

    statuses = [str(c.get("status") or "") for c in children]
    if any(s in {"pending", "running"} for s in statuses):
        if poc > 0:
            store.set_step_status(
                task_id,
                "step6_posts",
                "running",
                message=f"分平台发文采集中，已入库 {poc} 条",
            )
        return

    if any(s == "completed" for s in statuses) or poc > 0:
        store.set_step_status(
            task_id,
            "step6_posts",
            "completed",
            message=f"分平台发文采集结束，共 {poc} 条",
        )
    else:
        store.set_step_status(
            task_id,
            "step6_posts",
            "skipped",
            message="各平台发文均未采集",
        )


def reconcile_stuck_pipeline(store, task_id: str) -> None:
    """会话结束前兜底：收口卡住的 step2 / step4 / step5。"""
    s2 = get_step_status(task_id, "step2_cross_platform")
    if s2 in {"running", "failed"}:
        if _maigret_success_count(task_id) > 0:
            row = db.fetch_one(
                "SELECT COUNT(*) AS c FROM cross_platform_candidates WHERE task_id=%s",
                (task_id,),
            )
            n = int((row or {}).get("c") or 0)
            store.set_step_status(
                task_id,
                "step2_cross_platform",
                "completed",
                message=f"Maigret 跨平台扫描完成，候选 {n} 条",
            )

    s4img = get_step_status(task_id, "step4_image_compare")
    if s4img in {"pending", "running"}:
        if not is_image_compare_ready(task_id):
            n_skip = store.mark_remaining_image_streams_failed(task_id, "会话结束兜底：未完成 vision")
            if n_skip or count_image_streams(task_id) == 0:
                msg = (
                    "无头像图片流，跳过图片比对"
                    if count_image_streams(task_id) == 0
                    else f"图片流比对结束（兜底跳过 {n_skip} 条）"
                )
                store.set_step_status(task_id, "step4_image_compare", "completed", message=msg)
        else:
            store.set_step_status(task_id, "step4_image_compare", "completed", message="图片流 Vision 完成")

    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        return

    s4txt = get_step_status(task_id, "step4_text_compare")
    if s4txt in {"pending", "running"}:
        store.run_text_compare(task_id)

    s4txt = get_step_status(task_id, "step4_text_compare")
    s4img2 = get_step_status(task_id, "step4_image_compare")
    if s4txt == "completed" and s4img2 in {"completed", "skipped"}:
        if get_step_status(task_id, "step3_streams") != "completed":
            store.set_step_status(task_id, "step3_streams", "completed", message="文本/图片流拆分完成")
        if get_step_status(task_id, "step5_validated") != "completed":
            store.run_validated_accounts(task_id)
