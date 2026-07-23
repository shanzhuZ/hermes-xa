"""04 写报编排：推断当前步骤、工具白名单、进入分析阶段收口 step7。"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, FrozenSet, Optional

from collect_01 import db
from report_04.gates import get_step_status
from report_04.phases import (
    ANALYSIS_STEP_KEYS,
    APIFY_POST_TOOLS,
    PHASE_ANALYSIS,
    POST_PARENT_STEP_KEY,
    POST_TOOLS,
    PROFILE_TOOLS,
    STEP5_STREAM_TOOLS,
    WEB_SEARCH_TOOLS,
    root_step_keys,
)

logger = logging.getLogger(__name__)

# 各根步骤允许的工具（不含全局跳过的 clarify 等）
_STEP_WHITELIST: Dict[str, FrozenSet[str]] = {
    "step1_seed": frozenset(
        {
            "mcp_twitter_get_user_info",
            "mcp_youtube_get_channel_stats",
            "mcp_weibo_get_profile",
            "mcp_bilibili_get_user_info",
            "mcp_apify_get_actor_run",
            "mcp_apify_get_dataset_items",
            *{
                t
                for t in (
                    "mcp_apify_apify__instagram_scraper",
                    "mcp_apify_clockworks__tiktok_scraper",
                    "mcp_apify_vujeen__telegram_channel_scraper",
                    "mcp_apify_headlessagent__facebook_profile_post_scraper",
                    "mcp_apify_knotless_cadence__github_profile_scraper",
                )
            },
        }
    ),
    "step2_maigret": frozenset({"mcp_maigret_collect_accounts"}),
    "step3_web_search": WEB_SEARCH_TOOLS,
    "step4_profiles": PROFILE_TOOLS
    | frozenset({"mcp_apify_get_actor_run", "mcp_apify_get_dataset_items"})
    | APIFY_POST_TOOLS,
    "step5_streams": STEP5_STREAM_TOOLS,
    "step6_validated": frozenset(),  # 收敛账号，禁止采集类工具
    "step7_posts": POST_TOOLS | APIFY_POST_TOOLS | frozenset({"mcp_apify_get_actor_run", "mcp_apify_get_dataset_items"}),
    "step8_img_analysis": frozenset(),
    "step9_context_views": frozenset(),
    "step10_context_pii": frozenset(),
    "step11_report": frozenset(),
}

# 仅业务执行 key（不含 step_plan / 八大阶段壳）；壳长期 pending 不可参与门禁推断
_ROOT_ORDER = tuple(root_step_keys())


def infer_gate_step(task_id: str) -> str:
    """推断当前应处根的步骤（用于白名单）。"""
    # 步骤7 已收口 → 分析或报告
    s7 = get_step_status(task_id, "step7_posts")
    if s7 in {"completed", "skipped"}:
        for key in ANALYSIS_STEP_KEYS:
            st = get_step_status(task_id, key)
            if st in {"pending", "running"}:
                return key
        s11 = get_step_status(task_id, "step11_report")
        if s11 not in {"completed", "skipped"}:
            return "step11_report"
        return "step11_report"

    for key in _ROOT_ORDER:
        st = get_step_status(task_id, key)
        if st in {"pending", "running"}:
            return key
    return "step11_report"


def _allow_late_step7_post_collect(task_id: str, tool_name: str, phase: Optional[str]) -> bool:
    """步骤7 父节点已 completed 后允许补采发文（只入库，编排层禁止回开父节点）。"""
    if get_step_status(task_id, "step6_validated") != "completed":
        return False
    if get_step_status(task_id, "step7_posts") not in {"completed", "skipped"}:
        return False
    ph = str(phase or "")
    if tool_name in POST_TOOLS:
        return True
    if tool_name in APIFY_POST_TOOLS or tool_name == "mcp_apify_get_actor_run":
        return True
    if tool_name == "mcp_apify_get_dataset_items" and ph.startswith("step7_post_"):
        return True
    return False


def block_tool_reason(
    task_id: str,
    tool_name: str,
    *,
    phase: Optional[str] = None,
) -> Optional[str]:
    """当前步骤不允许该工具时返回拦截原因。"""
    if not tool_name:
        return None
    if _allow_late_step7_post_collect(task_id, tool_name, phase):
        return None

    gate = infer_gate_step(task_id)
    allowed = _STEP_WHITELIST.get(gate, frozenset())

    # Apify dataset/run：phase 与 gate 不一致时按 phase 放宽（Hook 已写 phase）
    ph = str(phase or "")
    if tool_name in {"mcp_apify_get_dataset_items", "mcp_apify_get_actor_run"}:
        if ph.startswith("step4_profile_") and gate in {"step4_profiles", "step1_seed"}:
            return None
        if ph.startswith("step7_post_") and gate in {"step7_posts", "step6_validated"}:
            return None

    if tool_name in allowed:
        return None

    # 步骤4 期间 YouTube 解析 UC 的 web 仍由 sink 专门处理，此处不重复
    if gate == "step3_web_search" and tool_name in WEB_SEARCH_TOOLS:
        return None

    return (
        f"当前编排步骤为 {gate}，禁止调用 {tool_name}。"
        f"请按写报顺序执行；若步骤7已收口，仅允许补采发文类工具。"
    )


def should_advance_on_tool(task_id: str, tool_name: str) -> bool:
    """步骤7未收口时，Agent 又调主页/检索类采集工具 → 视为进入分析前收口 step7。"""
    if not tool_name or get_step_status(task_id, "step6_validated") != "completed":
        return False
    if get_step_status(task_id, "step7_posts") in {"completed", "skipped"}:
        return False
    if tool_name in POST_TOOLS | APIFY_POST_TOOLS:
        return False
    if tool_name in {"mcp_apify_get_actor_run", "mcp_apify_get_dataset_items"}:
        return False
    if tool_name in WEB_SEARCH_TOOLS | PROFILE_TOOLS:
        return True
    return False


def advance_to_analysis_phase(store: Any, task_id: str, reason: str) -> bool:
    """
    进入步骤8/9/10：pending/running 的 step7 子步批量 skip，收口父节点，点亮分析步。
    幂等；步骤7 已 completed 时仍可点亮 8～10。
    """
    from report_04.step_reconcile import close_collect_parent_if_ready

    if get_step_status(task_id, "step6_validated") != "completed":
        return False

    changed = False
    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, POST_PARENT_STEP_KEY),
    )
    for row in rows:
        step_key = str(row.get("step_key") or "")
        st = str(row.get("status") or "")
        if st in {"pending", "running"}:
            store.set_step_status(
                task_id,
                step_key,
                "skipped",
                message=f"进入分析阶段收口：{reason[:120]}",
            )
            changed = True

    if close_collect_parent_if_ready(store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕"):
        changed = True

    if get_step_status(task_id, "step7_posts") not in {"completed", "skipped"}:
        return changed

    # 步骤7→8：图片资产兜底（Agent 主路径已跑则 skip_if_stored 秒回；禁止拖垮 Hook）
    try:
        from report_04.image_assets import run_image_pipeline_for_report

        run_image_pipeline_for_report(task_id, force_analyze=True, skip_if_stored=True)
    except Exception as exc:
        logger.warning("advance_to_analysis 图片管线兜底失败 task=%s: %s", task_id, exc)

    for step_key in ANALYSIS_STEP_KEYS:
        if get_step_status(task_id, step_key) == "pending":
            store.set_step_status(task_id, step_key, "running", message="分析进行中…")
            changed = True
    try:
        store.set_task_phase(task_id, PHASE_ANALYSIS)
    except Exception as exc:
        logger.warning("set_task_phase analysis 失败 task=%s: %s", task_id, exc)
    if changed:
        logger.info("advance_to_analysis_phase task=%s reason=%s", task_id, reason[:80])
    return changed


def on_post_tool_step7_close_parent(store: Any, task_id: str) -> None:
    """子步终态后尝试关闭 step7 父节点（毫秒级，post_tool 末尾调用）。"""
    from report_04.step_reconcile import close_collect_parent_if_ready

    close_collect_parent_if_ready(store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕")


def session_end_light(store: Any, task_id: str) -> None:
    """会话结束轻量收口：step7 子步 + 父节点 + 分析阶段触发，不跑全库 reconcile。"""
    from report_04.task_store import _reconcile_report_post_child_steps

    _reconcile_report_post_child_steps(store, task_id)
    store.reconcile_collect_child_steps(task_id)
    from report_04.step_reconcile import close_collect_parent_if_ready

    close_collect_parent_if_ready(store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕")


def run_full_reconcile_if_requested(store: Any, task_id: str) -> None:
    """仅当环境变量开启时跑重 reconcile（修复用，非正常写报路径）。"""
    if os.environ.get("HERMES_REPORT_FULL_RECONCILE", "").strip() not in {"1", "true", "yes"}:
        return
    from report_04.step_reconcile import reconcile_stuck_pipeline

    logger.info("HERMES_REPORT_FULL_RECONCILE=1，执行 reconcile_stuck_pipeline task=%s", task_id)
    reconcile_stuck_pipeline(store, task_id)
