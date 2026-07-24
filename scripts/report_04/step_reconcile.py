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
    can_advance_to_step5,
    can_close_step4_parent,
    can_complete_step11,
    can_advance_to_step7,
    can_run_step3_web_search,
    can_update_step4_children,
    count_image_streams,
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


def _task_seed_handle(task_id: str) -> str:
    row = db.fetch_one("SELECT seed_json FROM hermes_tasks WHERE task_id=%s", (task_id,))
    if not row:
        return ""
    try:
        seed = json.loads(row.get("seed_json") or "{}")
    except json.JSONDecodeError:
        return ""
    return str(seed.get("account_handle") or seed.get("account_hint") or "").strip()


def _task_seed_platform(task_id: str) -> str:
    row = db.fetch_one("SELECT seed_json FROM hermes_tasks WHERE task_id=%s", (task_id,))
    if not row:
        return ""
    try:
        seed = json.loads(row.get("seed_json") or "{}")
    except Exception:
        seed = {}
    return str((seed or {}).get("platform") or "").strip().lower()


def _profile_collect_attempted(task_id: str, platform: str) -> bool:
    if _apify_actor_success(task_id, platform):
        return True
    from report_04.phases import PROFILE_TOOLS, TOOL_PLATFORM

    for tool_name, plat in TOOL_PLATFORM.items():
        if plat != platform or tool_name not in PROFILE_TOOLS:
            continue
        row = db.fetch_one(
            """
            SELECT COUNT(*) AS c FROM hermes_tool_outputs
            WHERE task_id=%s AND tool_name=%s AND status='success'
            """,
            (task_id, tool_name),
        )
        if int((row or {}).get("c") or 0) > 0:
            return True
    return False


def _post_collect_attempted(task_id: str, platform: str) -> bool:
    """步骤七是否对该平台发起过正式发文采集。

    种子平台步骤一的 Apify（phase=step1_seed）不算发文尝试——步骤1只保留 profile。
    Apify：仅 Actor 成功不算「已采完」——必须有步骤七 dataset 成功，或 MCP 发文工具。
    """
    from report_04.phases import APIFY_TOOL_PLATFORM, TOOL_POST_PLATFORM

    mcp_tools = [t for t, p in TOOL_POST_PLATFORM.items() if p == platform]
    if mcp_tools:
        ph = ",".join(["%s"] * len(mcp_tools))
        row = db.fetch_one(
            f"""
            SELECT COUNT(*) AS c FROM hermes_tool_outputs
            WHERE task_id=%s AND tool_name IN ({ph})
            """,
            (task_id, *mcp_tools),
        )
        if int((row or {}).get("c") or 0) > 0:
            return True
    if _dataset_success_for_post(task_id, platform):
        return True
    return False


def _post_actor_without_dataset(task_id: str, platform: str) -> bool:
    """步骤七：Actor 已跑但未拉 dataset（常见于种子 Facebook 被 Agent 中途放弃）。"""
    from report_04.phases import APIFY_TOOL_PLATFORM

    if _dataset_success_for_post(task_id, platform):
        return False
    actor = ""
    for tool_name, plat in APIFY_TOOL_PLATFORM.items():
        if plat == platform:
            actor = tool_name
            break
    if not actor:
        return False
    post_key = post_platform_step_key(platform)
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s
          AND (phase=%s OR phase LIKE 'step7_post_%%')
        """,
        (task_id, actor, post_key),
    )
    return int((row or {}).get("c") or 0) > 0


def _latest_step7_dataset_row(task_id: str, platform: str) -> Dict[str, Any]:
    post_key = post_platform_step_key(platform)
    return db.fetch_one(
        """
        SELECT id, tool_output, tool_args FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items'
          AND status='success' AND phase=%s
        ORDER BY id DESC LIMIT 1
        """,
        (task_id, post_key),
    ) or {}


def _latest_step7_dataset_raw_count(task_id: str, platform: str) -> int:
    """步骤七最近一次 dataset 的原始条目数（用于区分真空 vs 解析未入库）。"""
    row = _latest_step7_dataset_row(task_id, platform)
    if not row:
        return 0
    from collect_01.normalizers.registry import dispatch

    raw = row.get("tool_output")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return 0
    try:
        args = json.loads(row.get("tool_args") or "{}")
    except json.JSONDecodeError:
        args = {}
    ctx = {
        "task_id": task_id,
        "tool_output_id": row.get("id"),
        "tool_name": "mcp_apify_get_dataset_items",
        "tool_args": args,
        "platform_hint": platform,
    }
    try:
        data = dispatch("mcp_apify_get_dataset_items", raw, ctx)
    except Exception:
        return 0
    raw_n = data.get("raw_item_count")
    if raw_n is not None:
        return int(raw_n or 0)
    return len(data.get("posts") or []) + len(data.get("profiles") or [])


def _replay_step7_dataset_for_platform(store: Any, task_id: str, platform: str) -> int:
    """回放步骤七 dataset，补入库并发文子步骤 completed（修复 reconcile 抢跑 skip）。"""
    from collect_01.normalizers.registry import dispatch

    post_key = post_platform_step_key(platform)
    existing = db.fetch_one(
        "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s AND platform=%s",
        (task_id, platform),
    )
    cnt = int((existing or {}).get("c") or 0)
    if cnt > 0:
        if get_step_status(task_id, post_key) != "completed":
            store.set_step_status(
                task_id,
                post_key,
                "completed",
                message=f"已入库发文 {cnt} 条",
                force_reopen=True,
            )
        return cnt

    row = _latest_step7_dataset_row(task_id, platform)
    if not row:
        return 0
    raw = row.get("tool_output")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return 0
    try:
        args = json.loads(row.get("tool_args") or "{}")
    except json.JSONDecodeError:
        args = {}
    ctx = {
        "task_id": task_id,
        "tool_output_id": row.get("id"),
        "tool_name": "mcp_apify_get_dataset_items",
        "tool_args": args,
        "platform_hint": platform,
    }
    try:
        data = dispatch("mcp_apify_get_dataset_items", raw, ctx)
    except Exception as exc:
        logger.warning("回放步骤七 dataset 失败 plat=%s task=%s: %s", platform, task_id, exc)
        return 0
    posts = data.get("posts") or []
    if not posts:
        return 0
    store.save_post_rows(posts, step_key=post_key)
    store.set_step_status(
        task_id,
        post_key,
        "completed",
        message=f"已入库发文 {len(posts)} 条",
        force_reopen=True,
    )
    return len(posts)


def _step7_sibling_has_progress(task_id: str, platform: str) -> bool:
    """其他平台步骤七已有实质进展（completed / 有发文 / dataset 成功）。"""
    post_counts = _post_counts(task_id)
    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s AND step_key LIKE 'step7_post_%%'
        """,
        (task_id, POST_PARENT_STEP_KEY),
    )
    for row in rows:
        step_key = str(row.get("step_key") or "")
        if not step_key.startswith("step7_post_"):
            continue
        plat = step_key.replace("step7_post_", "", 1)
        if plat == platform:
            continue
        st = str(row.get("status") or "")
        if st == "completed" or post_counts.get(plat, 0) > 0:
            return True
        if _dataset_success_for_post(task_id, plat):
            return True
    return False


def _maybe_skip_step7_actor_stale(store: Any, task_id: str, platform: str, cur: str) -> bool:
    """Actor 已跑但未拉 dataset，且其他平台已在步骤七推进 → 勿长期占 running。"""
    if cur not in {"running", "pending"}:
        return False
    if not _post_actor_without_dataset(task_id, platform):
        return False
    if not _step7_sibling_has_progress(task_id, platform):
        return False
    step_key = post_platform_step_key(platform)
    store.set_step_status(
        task_id,
        step_key,
        "skipped",
        message=f"{platform} Actor 已完成但未拉取发文 dataset",
    )
    return True


def force_skip_unattempted_step4_children(store: Any, task_id: str) -> int:
    """会话结束兜底：跳过仍未尝试主页采集的步骤四子节点（禁止中途 peer-skip）。"""
    if not discovery_steps_terminal(task_id):
        return 0
    updated = 0
    rows = db.fetch_all(
        "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    for row in rows:
        step_key = str(row.get("step_key") or "")
        if not step_key.startswith("step4_profile_"):
            continue
        cur = str(row.get("status") or "")
        if cur not in {"pending", "running"}:
            continue
        plat = step_key.replace("step4_profile_", "", 1)
        if _prof_counts(task_id).get(plat, 0) > 0:
            continue
        if _profile_collect_attempted(task_id, plat):
            continue
        store.set_step_status(
            task_id,
            step_key,
            "skipped",
            message=f"{plat} 未执行主页采集，已跳过",
        )
        updated += 1
    return updated


def maybe_close_abandoned_step4(
    store: Any,
    task_id: str,
    *,
    min_quiet_seconds: float = 90.0,
    force: bool = False,
) -> int:
    """中途收口：Agent 已进入步骤五，或步骤四工具长时间无进展时，跳过未尝试子节点并关闭父节点。

    保护条件（降低误伤）：
    - 步骤二、三已终态
    - 父节点尚未 completed/skipped
    - 至少已有一个 step4 子节点 completed（说明主页采集已真实开始过）
    - force=True，或步骤五已 running/completed，或距上次 step4_* 成功工具 ≥ min_quiet_seconds
    """
    from report_04.gates import seconds_since_last_tool

    if not discovery_steps_terminal(task_id):
        return 0
    parent_st = get_step_status(task_id, PROFILE_PARENT_STEP_KEY)
    if parent_st in {"completed", "skipped"}:
        return 0

    children = db.fetch_all(
        "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    has_completed_child = any(str(r.get("status") or "") == "completed" for r in children)
    has_open_child = any(str(r.get("status") or "") in {"pending", "running"} for r in children)
    if not has_completed_child:
        return 0
    if not has_open_child:
        return close_collect_parent_if_ready(
            store, task_id, PROFILE_PARENT_STEP_KEY, "候选主页采集已尝试完毕"
        )

    s5 = get_step_status(task_id, "step5_streams")
    moved_on = s5 in {"running", "completed"}
    age = seconds_since_last_tool(task_id, phase_prefix="step4_")
    quiet = age is not None and age >= float(min_quiet_seconds)
    if not force and not moved_on and not quiet:
        return 0

    updated = force_skip_unattempted_step4_children(store, task_id)
    updated += close_collect_parent_if_ready(
        store, task_id, PROFILE_PARENT_STEP_KEY, "候选主页采集已尝试完毕"
    )
    if updated:
        logger.info(
            "abandoned step4 收口 task=%s force=%s moved_on=%s quiet=%s age=%s updated=%s",
            task_id,
            force,
            moved_on,
            quiet,
            age,
            updated,
        )
    return updated


def _skip_irrelevant_step4_children(store: Any, task_id: str) -> int:
    """跳过与种子无关的步骤四子节点。"""
    from report_04.candidate_parser import candidate_relevant_for_seed, relevant_profile_platforms

    rows = db.fetch_all(
        """
        SELECT platform, account_handle, match_strategy FROM cross_platform_candidates
        WHERE task_id=%s AND platform IS NOT NULL AND TRIM(platform) != ''
        """,
        (task_id,),
    )
    relevant = set(relevant_profile_platforms(rows, _task_seed_handle(task_id)))
    updated = 0
    children = db.fetch_all(
        "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    for row in children:
        step_key = str(row.get("step_key") or "")
        if not step_key.startswith("step4_profile_"):
            continue
        plat = step_key.replace("step4_profile_", "", 1)
        cur = str(row.get("status") or "pending")
        if plat in relevant or cur not in {"pending", "running"}:
            continue
        if not is_collectible_platform(plat):
            continue
        # 有任一相关候选则保留
        if any(
            str(c.get("platform") or "").lower() == plat and candidate_relevant_for_seed(c, _task_seed_handle(task_id))
            for c in rows
        ):
            continue
        store.set_step_status(
            task_id,
            step_key,
            "skipped",
            message=f"{plat} 候选与种子账号不匹配，跳过",
        )
        updated += 1
    return updated


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


def reconcile_step7_from_post_tools(store: Any, task_id: str) -> int:
    """回放步骤七发文工具输出，补入库 collect_posts（修复提前调工具时未入库）。"""
    if get_step_status(task_id, "step6_validated") != "completed":
        return 0
    from collect_01.normalizers.registry import dispatch
    from report_04.phases import POST_TOOLS, TOOL_PLATFORM, post_platform_step_key

    total = 0
    for tool_name in POST_TOOLS:
        if tool_name == "mcp_apify_get_dataset_items":
            rows = db.fetch_all(
                """
                SELECT id, tool_output, tool_args, phase FROM hermes_tool_outputs
                WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items' AND status='success'
                  AND phase LIKE 'step7_post_%%'
                ORDER BY id
                """,
                (task_id,),
            )
        else:
            rows = db.fetch_all(
                """
                SELECT id, tool_output, tool_args, phase FROM hermes_tool_outputs
                WHERE task_id=%s AND tool_name=%s AND status='success'
                ORDER BY id
                """,
                (task_id, tool_name),
            )
        if not rows:
            continue
        platform = ""
        if tool_name == "mcp_apify_get_dataset_items":
            phase = str(rows[-1].get("phase") or "")
            if phase.startswith("step7_post_"):
                platform = phase.replace("step7_post_", "", 1)
        else:
            platform = TOOL_PLATFORM.get(tool_name) or ""
        if not platform:
            continue
        existing = db.fetch_one(
            "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s AND platform=%s",
            (task_id, platform),
        )
        if int((existing or {}).get("c") or 0) > 0:
            continue
        for row in rows:
            raw = row.get("tool_output")
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    pass
            try:
                args = json.loads(row.get("tool_args") or "{}")
            except json.JSONDecodeError:
                args = {}
            ctx = {
                "task_id": task_id,
                "tool_output_id": row["id"],
                "tool_name": tool_name,
                "tool_args": args,
            }
            try:
                data = dispatch(tool_name, raw, ctx)
            except Exception as exc:
                logger.warning("回放发文工具失败 tool=%s task=%s: %s", tool_name, task_id, exc)
                continue
            posts = data.get("posts") or []
            if posts:
                store.save_post_rows(posts, step_key=post_platform_step_key(platform))
                total += len(posts)
                break
    return total


def ensure_step7_parent_active(store: Any, task_id: str) -> int:
    """步骤六完成后，纠正 step7_posts 与子节点不一致。

    - 误标 skipped 时按子节点回正
    - 父已 completed 但仍有 pending/running 子节点时回开（晚到平台补采）
    - 仅有 pending 子节点时不得把父节点点成 running（等首个发文工具）—— 晚到 pending 除外用 pending 回开
    """
    if get_step_status(task_id, "step6_validated") != "completed":
        return 0
    rows = db.fetch_all(
        "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, POST_PARENT_STEP_KEY),
    )
    if not rows:
        return 0
    cur = get_step_status(task_id, "step7_posts")
    has_running = any(str(r.get("status") or "") == "running" for r in rows)
    has_done = any(str(r.get("status") or "") == "completed" for r in rows)
    has_open_pending = any(str(r.get("status") or "") == "pending" for r in rows)
    # 晚到子节点：父已收口后仍有未终态子步 → 必须回开（对齐 step4 ensure_step4_parent_not_premature）
    if cur == "completed" and (has_running or has_open_pending):
        store.set_step_status(
            task_id,
            "step7_posts",
            "running" if has_running else "pending",
            message="发文采集中（晚到补采）" if has_running else "等待发文采集（晚到子节点）",
            force_reopen=True,
        )
        logger.info(
            "step7 晚到子节点，回开父步骤 task=%s running=%s pending=%s",
            task_id,
            has_running,
            has_open_pending,
        )
        return 1
    if cur == "skipped" and (has_running or has_done):
        if has_running or has_open_pending:
            store.set_step_status(task_id, "step7_posts", "running" if has_running else "pending", message="发文采集中" if has_running else "等待发文采集")
        else:
            store.set_step_status(task_id, "step7_posts", "completed", message="发文采集已尝试完毕")
        return 1
    if cur == "pending" and has_running:
        store.set_step_status(task_id, "step7_posts", "running", message="发文采集中")
        return 1
    if cur in {"pending", "running"} and has_done and not has_open_pending and not has_running:
        store.set_step_status(task_id, "step7_posts", "completed", message="发文采集已尝试完毕")
        return 1
    return 0


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
                # 种子平台已在步骤一入库时，先点亮父步骤四，再允许对应子节点直接 completed。
                # 否则会出现 step4_profile_facebook 先完成、step4_profiles 后 running 的错序。
                if get_step_status(task_id, PROFILE_PARENT_STEP_KEY) in {"pending", ""}:
                    store.set_step_status(
                        task_id,
                        PROFILE_PARENT_STEP_KEY,
                        "running",
                        message="候选主页采集中",
                    )
                seed_plat = _task_seed_platform(task_id)
                msg = (
                    f"种子主页已在步骤一入库（{cnt} 条）"
                    if plat and plat == seed_plat
                    else f"已入库主页 {cnt} 条"
                )
                store.set_step_status(task_id, step_key, "completed", message=msg)
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
            # 禁止中途 peer-skip：未轮到的平台保持 pending，等 Agent 采完或 session_end 兜底
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
            from report_04.video_job import finalize_post_platform_after_posts

            finalize_post_platform_after_posts(
                store,
                task_id,
                plat,
                post_count=cnt,
                force_reopen=(cur == "skipped"),
            )
            updated += 1
            continue
        # 发文子步仍 running（等视频）：视频已终态则补收口
        if cnt > 0 and cur == "running":
            from report_04.video_job import complete_post_after_video

            before = get_step_status(task_id, step_key)
            complete_post_after_video(store, task_id, plat)
            if get_step_status(task_id, step_key) != before:
                updated += 1
                continue
        if _maybe_skip_step7_actor_stale(store, task_id, plat, cur):
            updated += 1
            continue
        if (
            cnt == 0
            and cur in {"running", "pending"}
            and _dataset_success_for_post(task_id, plat)
        ):
            n_replay = _replay_step7_dataset_for_platform(store, task_id, plat)
            if n_replay > 0:
                updated += 1
                continue
            raw_n = _latest_step7_dataset_raw_count(task_id, plat)
            if raw_n > 0:
                if cur == "pending":
                    store.set_step_status(
                        task_id,
                        step_key,
                        "running",
                        message=f"{plat} dataset 已拉取 {raw_n} 条，解析入库中…",
                    )
                    updated += 1
                continue
            if cur != "skipped":
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


def reconcile_step1_from_twitter(store: Any, task_id: str) -> int:
    """回放种子 profile 工具（MCP + Apify dataset），补入库并完成步骤一。"""
    if get_step_status(task_id, "step1_seed") in {"completed", "skipped", "failed"}:
        return 0
    task = store.get_task(task_id) or {}
    if str(task.get("status") or "") in {"failed", "completed"}:
        return 0
    from collect_01.normalizers.base import normalize_mcp_tool_name
    from collect_01.normalizers.registry import dispatch
    from collect_01.seed_platforms import (
        APIFY_SEED_PLATFORM_TOOLS,
        MCP_SEED_PLATFORM_TOOLS,
        apify_seed_empty_ok,
        is_apify_seed_platform,
    )

    try:
        seed = json.loads(task.get("seed_json") or "{}")
    except Exception:
        seed = {}
    seed_plat = str(seed.get("platform") or "twitter").lower()

    mcp_tools = list(MCP_SEED_PLATFORM_TOOLS.values())
    rows = db.fetch_all(
        """
        SELECT id, tool_name, tool_output FROM hermes_tool_outputs
        WHERE task_id=%s AND status='success'
          AND (
            tool_name IN ({mcp_ph})
            OR tool_name = 'mcp_apify_get_dataset_items'
          )
        ORDER BY id
        """.format(mcp_ph=",".join(["%s"] * len(mcp_tools))),
        (task_id, *mcp_tools),
    )
    if not rows:
        return 0
    for row in rows:
        tool_name = normalize_mcp_tool_name(str(row["tool_name"]))
        if tool_name == "mcp_apify_get_dataset_items" and not is_apify_seed_platform(seed_plat):
            continue
        if apify_seed_empty_ok(tool_name):
            continue
        ctx = {
            "task_id": task_id,
            "tool_output_id": row["id"],
            "tool_name": tool_name,
            "platform_hint": APIFY_SEED_PLATFORM_TOOLS.get(seed_plat, "").replace("mcp_apify_", "")
            if tool_name == "mcp_apify_get_dataset_items"
            else "",
        }
        data = dispatch(tool_name, row.get("tool_output"), ctx)
        profs = data.get("profiles") or []
        if not profs:
            continue
        store.mark_seed_completed(task_id, profs, source="tool")
        return 1
    return 0


def reconcile_step2_from_maigret(store: Any, task_id: str) -> int:
    """回放 Maigret 工具输出，补入库候选并完成步骤二。"""
    if get_step_status(task_id, "step2_maigret") in {"completed", "skipped"}:
        return 0
    from collect_01.normalizers.base import normalize_mcp_tool_name

    rows = db.fetch_all(
        """
        SELECT id, tool_name, tool_output FROM hermes_tool_outputs
        WHERE task_id=%s AND status='success'
          AND (tool_name LIKE 'mcp_maigret_%%' OR tool_name LIKE 'mcp__maigret__%%')
        ORDER BY id
        """,
        (task_id,),
    )
    if not rows:
        return 0
    from collect_01.normalizers.registry import dispatch

    total = 0
    for row in rows:
        tool_name = normalize_mcp_tool_name(str(row["tool_name"]))
        ctx = {
            "task_id": task_id,
            "tool_output_id": row["id"],
            "tool_name": tool_name,
        }
        data = dispatch(tool_name, row.get("tool_output"), ctx)
        cands = data.get("candidates") or []
        if not cands:
            continue
        for c in cands:
            c["task_id"] = task_id
            c.setdefault("match_strategy", "maigret")
        store.save_candidate_rows(cands, step_key="step2_maigret")
        total += len(cands)
    if total > 0:
        store.set_step_status(
            task_id,
            "step2_maigret",
            "completed",
            message=f"Maigret 发现 {total} 个候选",
        )
        return 1
    return 0


def reconcile_step3_from_web_tools(store: Any, task_id: str) -> int:
    """回放 web 工具输出，补入库候选并完成步骤三。"""
    if get_step_status(task_id, "step3_web_search") in {"completed", "skipped"}:
        return 0
    if not can_run_step3_web_search(task_id):
        return 0

    from report_04.candidate_parser import parse_web_search_candidates_from_tool
    from report_04.phases import WEB_SEARCH_TOOLS

    rows = db.fetch_all(
        """
        SELECT tool_name, tool_output FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name IN %s AND status='success'
        ORDER BY id
        """,
        (task_id, tuple(WEB_SEARCH_TOOLS)),
    )
    if not rows:
        return 0

    total = 0
    for row in rows:
        tool_name = str(row.get("tool_name") or "")
        raw = row.get("tool_output")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        cands = parse_web_search_candidates_from_tool(tool_name, raw)
        if not cands:
            continue
        for c in cands:
            c["task_id"] = task_id
            c.setdefault("match_strategy", "web_search")
        store.save_candidate_rows(cands, step_key="step3_web_search")
        total += len(cands)

    stat = db.fetch_one(
        """
        SELECT
          SUM(tool_name='web_search' AND status='success') AS n_search,
          SUM(tool_name='web_extract' AND status='success') AS n_extract
        FROM hermes_tool_outputs
        WHERE task_id=%s AND phase='step3_web_search'
        """,
        (task_id,),
    )
    n_search = int((stat or {}).get("n_search") or 0)
    n_extract = int((stat or {}).get("n_extract") or 0)

    should_complete = n_extract >= 1 or n_search >= 3
    if not should_complete:
        return 0
    # 会话收口才强制完成；中途由 sink 安静期控制
    age = None
    try:
        from report_04.gates import seconds_since_last_tool

        age = seconds_since_last_tool(task_id, phase_prefix="step3_web_search")
    except Exception:
        age = None
    if age is not None and age < 40:
        return 0

    n_web = db.fetch_one(
        "SELECT COUNT(*) AS c FROM cross_platform_candidates WHERE task_id=%s AND match_strategy='web_search'",
        (task_id,),
    )
    n_web_c = int((n_web or {}).get("c") or 0)
    store.set_step_status(
        task_id,
        "step3_web_search",
        "completed",
        message=f"网页检索完成（search={n_search} extract={n_extract} 候选={n_web_c}）",
    )
    store.materialize_step4_from_candidates(task_id)
    return 1


def ensure_step5_not_premature(store: Any, task_id: str) -> int:
    """步骤四未完成前不得将步骤五标为 running/completed。"""
    if can_advance_to_step5(task_id).get("ok"):
        return 0
    cur = get_step_status(task_id, "step5_streams")
    if cur in {"running", "completed"}:
        store.set_step_status(task_id, "step5_streams", "pending", message="等待步骤四完成")
        return 1
    return 0


def ensure_step6_not_premature(store: Any, task_id: str) -> int:
    """步骤五未完成前不得将步骤六标为 running/completed。"""
    if get_step_status(task_id, "step5_streams") in {"completed", "skipped"}:
        return 0
    cur = get_step_status(task_id, "step6_validated")
    if cur in {"running", "completed"}:
        store.set_step_status(task_id, "step6_validated", "pending", message="等待步骤五完成")
        return 1
    return 0


def ensure_step3_not_premature(store: Any, task_id: str) -> int:
    """步骤二完成前不得将步骤三标为 running/completed。"""
    if can_run_step3_web_search(task_id):
        return 0
    cur = get_step_status(task_id, "step3_web_search")
    if cur in {"running", "completed"}:
        store.set_step_status(task_id, "step3_web_search", "pending", message="等待步骤二完成")
        return 1
    return 0


def ensure_step4_children_not_premature(store: Any, task_id: str) -> int:
    """步骤二、三完成前不得存在步骤四主页子节点。"""
    if can_update_step4_children(task_id):
        return 0
    rows = db.fetch_all(
        "SELECT step_key FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    n = 0
    for row in rows:
        step_key = str(row.get("step_key") or "")
        if step_key:
            db.execute(
                "DELETE FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
                (task_id, step_key),
            )
            n += 1
    return n


def ensure_step4_parent_not_premature(store: Any, task_id: str) -> int:
    """纠正步骤四在步骤二、三完成前被误标；晚到子节点时回开父节点并回滚过早的步骤五。"""
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
    if not has_active_child:
        return 0
    # 晚到子节点：父步骤必须回 running，并回滚过早的步骤五（六/七由 ensure_*_not_premature 连锁纠正）
    store.set_step_status(
        task_id,
        parent,
        "running",
        message="候选主页采集中（晚到补采）",
        force_reopen=True,
    )
    rolled = 1
    s5 = get_step_status(task_id, "step5_streams")
    if s5 in {"running", "completed", "skipped"}:
        store.set_step_status(task_id, "step5_streams", "pending", message="等待步骤四完成")
        rolled += 1
    ensure_step6_not_premature(store, task_id)
    ensure_step7_parent_not_premature(store, task_id)
    logger.info("step4 晚到子节点，回开父步骤并回滚后续 task=%s", task_id)
    return rolled


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
    closed = 0
    if get_step_status(task_id, parent) not in {"completed", "skipped"}:
        store.set_step_status(task_id, parent, "completed", message=msg_done)
        closed = 1
    # 步骤四一旦终态，立刻踢步骤五，避免仅等 Agent 调 vision 而长期 pending
    if parent == PROFILE_PARENT_STEP_KEY and get_step_status(task_id, parent) in {"completed", "skipped"}:
        try:
            store.kickoff_step5_if_ready(task_id)
        except Exception as exc:
            logger.warning("kickoff_step5 失败 task=%s: %s", task_id, exc)
    return closed


def _normalize_stored_mcp_tool_names(task_id: str) -> int:
    """把库里已写入的 mcp__* 工具名改成 registry 使用的 mcp_* 形式。"""
    from collect_01.normalizers.base import normalize_mcp_tool_name

    rows = db.fetch_all(
        "SELECT id, tool_name FROM hermes_tool_outputs WHERE task_id=%s AND tool_name LIKE 'mcp__%%'",
        (task_id,),
    )
    n = 0
    for row in rows:
        old = str(row.get("tool_name") or "")
        new = normalize_mcp_tool_name(old)
        if new != old:
            db.execute(
                "UPDATE hermes_tool_outputs SET tool_name=%s WHERE id=%s",
                (new, row["id"]),
            )
            n += 1
    return n


def _fail_forward_step5_pending_images(store: Any, task_id: str, *, reason: str = "会话结束兜底：未完成 vision") -> int:
    """步骤五卡在等待 OCR/Vision 时，兜底关闭剩余 pending 图片流并 completed。"""
    from report_04.gates import step4_profiles_terminal

    if not step4_profiles_terminal(task_id):
        return 0
    if not can_advance_to_step5(task_id).get("ok"):
        return 0
    s5 = get_step_status(task_id, "step5_streams")
    if s5 not in {"pending", "running"}:
        return 0
    if is_stream_compare_ready(task_id):
        n_img = count_image_streams(task_id)
        msg = "无头像图片流，跳过图片比对" if n_img == 0 else "图片流 Vision 完成"
        store.set_step_status(task_id, "step5_streams", "completed", message=msg)
        return 0
    n_skip = 0
    if hasattr(store, "mark_remaining_image_streams_failed"):
        n_skip = int(store.mark_remaining_image_streams_failed(task_id, reason) or 0)
    n_img = count_image_streams(task_id)
    if n_skip or n_img == 0 or is_stream_compare_ready(task_id):
        msg = (
            "无头像图片流，跳过图片比对"
            if n_img == 0
            else f"图片流比对结束（兜底跳过 {n_skip} 条）"
        )
        store.set_step_status(task_id, "step5_streams", "completed", message=msg)
        logger.info("step5 图片流兜底收口 task=%s skipped=%s reason=%s", task_id, n_skip, reason)
        try:
            if get_step_status(task_id, "step6_validated") != "completed":
                store.run_validated_accounts(task_id)
            # 仅预建子节点，不点亮步骤7 running
            if get_step_status(task_id, "step6_validated") == "completed":
                store.prepare_step7_children_pending(task_id)
                reconcile_step4_and_step7_children(store, task_id)
        except Exception as exc:
            logger.warning("step5 兜底后 validated/step7 失败 task=%s: %s", task_id, exc)
    return n_skip


def maybe_fail_forward_stale_step5(
    store: Any,
    task_id: str,
    *,
    min_wait_seconds: int = 90,
) -> int:
    """
    会话中途兜底：文本流已比对完、仍等图片流 Vision，但模型长时间未写回图片流。
    仅依赖 session_end 时无法解开「Agent 还在跑、但从不调 vision」的死锁。
    """
    from report_04.gates import step4_profiles_terminal

    # 步骤四父/子未真正终态时禁止兜底（否则会出现步骤五「完成」后 stream 才出 vision）
    if not step4_profiles_terminal(task_id):
        return 0
    if not can_advance_to_step5(task_id).get("ok"):
        return 0
    s5 = get_step_status(task_id, "step5_streams")
    if s5 != "running":
        return 0
    if is_stream_compare_ready(task_id):
        n_img = count_image_streams(task_id)
        msg = "无头像图片流，跳过图片比对" if n_img == 0 else "图片流 Vision 完成"
        store.set_step_status(task_id, "step5_streams", "completed", message=msg)
        if get_step_status(task_id, "step6_validated") != "completed":
            store.run_validated_accounts(task_id)
        return 0

    row = db.fetch_one(
        """
        SELECT message, updated_at, payload_json
        FROM collect_phase_steps
        WHERE task_id=%s AND step_key='step5_streams'
        """,
        (task_id,),
    )
    if not row:
        return 0
    msg = str(row.get("message") or "")
    # 仅在已进入「等图片流」阶段后才超时跳过，避免文本比对进行中误杀
    waiting_images = ("等待图片流" in msg) or ("OCR/Vision" in msg) or ("图片流比对中" in msg)
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if int(payload.get("image_pending") or 0) > 0 or payload.get("text_compare_done"):
        waiting_images = True
    if not waiting_images and count_image_streams(task_id) == 0:
        return 0
    if not waiting_images:
        return 0

    # 用首次进入「等图片」的时间计时，禁止被 message 刷新清零
    from datetime import datetime

    age = 0.0
    wait_since = str(payload.get("wait_images_since") or "").strip()
    if wait_since:
        try:
            started = datetime.fromisoformat(wait_since)
            age = (datetime.now() - started).total_seconds()
        except Exception:
            age = 0.0
    if age <= 0:
        updated = row.get("updated_at")
        if updated is not None and hasattr(updated, "year"):
            try:
                age = (datetime.now() - updated).total_seconds()
            except Exception:
                age = 0.0
    if age < float(min_wait_seconds):
        return 0

    return _fail_forward_step5_pending_images(
        store,
        task_id,
        reason=f"中途超时兜底：步骤五等待 Vision 超过 {min_wait_seconds}s 仍未完成",
    )


def reconcile_stuck_pipeline(store: Any, task_id: str) -> None:
    # 新版 Hermes 工具名 mcp__server__tool → 统一为 mcp_server_tool 后再回放
    _normalize_stored_mcp_tool_names(task_id)
    reconcile_step1_from_twitter(store, task_id)
    reconcile_step2_from_maigret(store, task_id)
    reconcile_step3_from_web_tools(store, task_id)
    ensure_step3_not_premature(store, task_id)
    ensure_step4_children_not_premature(store, task_id)
    maybe_close_abandoned_step4(store, task_id)
    _skip_irrelevant_step4_children(store, task_id)
    reconcile_step4_and_step7_children(store, task_id)
    # 会话收口路径：才跳过未尝试的步骤四子节点（中途禁止 peer-skip）
    force_skip_unattempted_step4_children(store, task_id)
    reconcile_step4_and_step7_children(store, task_id)
    ensure_step5_not_premature(store, task_id)
    ensure_step6_not_premature(store, task_id)
    if get_step_status(task_id, "step6_validated") == "completed":
        reconcile_step7_from_post_tools(store, task_id)
        ensure_step7_parent_active(store, task_id)
        reconcile_step4_and_step7_children(store, task_id)
    ensure_step7_parent_not_premature(store, task_id)
    _reconcile_vision_from_tools(store, task_id)

    for parent, msg_done in (
        ("step4_profiles", "候选主页采集已尝试完毕"),
        ("step7_posts", "发文采集已尝试完毕"),
    ):
        close_collect_parent_if_ready(store, task_id, parent, msg_done)

    # vision 回放后再校正；并强制尝试收口 step5（图片流已补齐时）
    ensure_step5_not_premature(store, task_id)
    ensure_step6_not_premature(store, task_id)
    ensure_step7_parent_not_premature(store, task_id)

    # 会话结束兜底：步骤五仍等 vision 时，把 pending 图片流标 fail 后强制收口（对齐 01/03）
    _fail_forward_step5_pending_images(store, task_id)

    if can_advance_to_step5(task_id).get("ok"):
        store.kickoff_step5_if_ready(task_id)
        # 会话收口：图片流已齐则允许完成步骤5（不再等 settle）
        if (
            is_stream_compare_ready(task_id)
            and get_step_status(task_id, "step5_streams") == "running"
        ):
            n_img = count_image_streams(task_id)
            msg = "无头像图片流，跳过图片比对" if n_img == 0 else "图片流 Vision 完成"
            store.set_step_status(task_id, "step5_streams", "completed", message=msg)
        if (
            get_step_status(task_id, "step5_streams") in {"completed", "skipped"}
            and get_step_status(task_id, "step6_validated") != "completed"
        ):
            store.run_validated_accounts(task_id)

    if can_advance_to_step7(task_id).get("ok"):
        reconcile_step7_from_post_tools(store, task_id)
        store.prepare_step7_children_pending(task_id)
        ensure_step7_parent_active(store, task_id)
        reconcile_step4_and_step7_children(store, task_id)

    if can_advance_to_analysis(task_id).get("ok"):
        if get_step_status(task_id, "step5_streams") != "completed":
            store.kickoff_step5_if_ready(task_id)

    if analysis_steps_terminal(task_id) and can_complete_step11(task_id).get("ok"):
        if get_step_status(task_id, "step11_report") != "completed":
            summary = db.fetch_one(
                "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
                (task_id,),
            )
            if summary:
                store.set_step_status(task_id, "step11_report", "completed", message="画像报告已生成")
