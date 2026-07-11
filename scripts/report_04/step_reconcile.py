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


def _step4_siblings_terminal(task_id: str, except_step_key: str) -> bool:
    rows = db.fetch_all(
        "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    for row in rows:
        sk = str(row.get("step_key") or "")
        if sk == except_step_key:
            continue
        if str(row.get("status") or "") in {"pending", "running"}:
            return False
    return True


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
    """步骤六完成后，纠正 step7_posts 被误标 skipped/pending 的情况。"""
    if get_step_status(task_id, "step6_validated") != "completed":
        return 0
    rows = db.fetch_all(
        "SELECT step_key, status FROM collect_phase_steps WHERE task_id=%s AND parent_step_key=%s",
        (task_id, POST_PARENT_STEP_KEY),
    )
    if not rows:
        return 0
    cur = get_step_status(task_id, "step7_posts")
    has_open = any(str(r.get("status") or "") in {"pending", "running"} for r in rows)
    has_done = any(str(r.get("status") or "") == "completed" for r in rows)
    if cur == "skipped" and (has_open or has_done):
        if has_open:
            store.set_step_status(task_id, "step7_posts", "running", message="发文采集中")
        else:
            store.set_step_status(task_id, "step7_posts", "completed", message="发文采集已尝试完毕")
        return 1
    if cur == "pending" and (has_open or has_done):
        store.set_step_status(
            task_id,
            "step7_posts",
            "running" if has_open else "completed",
            message="发文采集中" if has_open else "发文采集已尝试完毕",
        )
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
            if (
                cur == "pending"
                and cnt == 0
                and not _profile_collect_attempted(task_id, plat)
                and _step4_siblings_terminal(task_id, step_key)
            ):
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"{plat} 未执行主页采集，已跳过",
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


def reconcile_step1_from_twitter(store: Any, task_id: str) -> int:
    """回放 Twitter 种子 profile 工具，补入库并完成步骤一。"""
    if get_step_status(task_id, "step1_seed") in {"completed", "skipped"}:
        return 0
    from collect_01.normalizers.base import normalize_mcp_tool_name
    from collect_01.normalizers.registry import dispatch

    rows = db.fetch_all(
        """
        SELECT id, tool_name, tool_output FROM hermes_tool_outputs
        WHERE task_id=%s AND status='success'
          AND (
            tool_name IN ('mcp_twitter_get_user_info', 'mcp__twitter__get_user_info')
            OR tool_name LIKE 'mcp_twitter_get_user_info%%'
            OR tool_name LIKE 'mcp__twitter__get_user_info%%'
          )
        ORDER BY id
        """,
        (task_id,),
    )
    if not rows:
        return 0
    for row in rows:
        tool_name = normalize_mcp_tool_name(str(row["tool_name"]))
        ctx = {
            "task_id": task_id,
            "tool_output_id": row["id"],
            "tool_name": tool_name,
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

    should_complete = n_extract >= 1 or n_search >= 2
    if not should_complete:
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


def reconcile_stuck_pipeline(store: Any, task_id: str) -> None:
    # 新版 Hermes 工具名 mcp__server__tool → 统一为 mcp_server_tool 后再回放
    _normalize_stored_mcp_tool_names(task_id)
    reconcile_step1_from_twitter(store, task_id)
    reconcile_step2_from_maigret(store, task_id)
    reconcile_step3_from_web_tools(store, task_id)
    ensure_step3_not_premature(store, task_id)
    ensure_step4_children_not_premature(store, task_id)
    _skip_irrelevant_step4_children(store, task_id)
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

    # 步骤四收口后再次校正，避免 vision 回放误推进
    ensure_step5_not_premature(store, task_id)
    ensure_step6_not_premature(store, task_id)
    ensure_step7_parent_not_premature(store, task_id)

    if can_advance_to_step5(task_id).get("ok"):
        if get_step_status(task_id, "step5_streams") not in {"completed", "skipped"}:
            if is_stream_compare_ready(task_id):
                store.set_step_status(task_id, "step5_streams", "completed", message="流核查完成")
            else:
                store.run_stream_validation(task_id)
        if (
            get_step_status(task_id, "step5_streams") in {"completed", "skipped"}
            and get_step_status(task_id, "step6_validated") != "completed"
        ):
            store.run_validated_accounts(task_id)

    if can_advance_to_step7(task_id).get("ok"):
        reconcile_step7_from_post_tools(store, task_id)
        ensure_step7_parent_active(store, task_id)
        store.materialize_step7_from_validated(task_id)
        reconcile_step4_and_step7_children(store, task_id)

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
