"""03 账号核查 — 步骤收口与卡死恢复。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from collect_01 import db
from collect_01.normalizers.apify import APIFY_TOOL_PLATFORM
from verify_03.gates import (
    can_advance_to_step45,
    count_image_streams,
    get_step_status,
    is_image_compare_ready,
)
from verify_03.phases import (
    PROFILE_PARENT_STEP_KEY,
    profile_platform_step_key,
)

logger = logging.getLogger(__name__)

POST_TOOL_BY_PLATFORM: Dict[str, str] = {
    "twitter": "mcp_twitter_get_user_tweets",
    "youtube": "mcp_youtube_analyze_channel_videos",
    "weibo": "mcp_weibo_get_feeds",
}

PROFILE_TOOL_BY_PLATFORM: Dict[str, str] = {
    "twitter": "mcp_twitter_get_user_info",
    "youtube": "mcp_youtube_get_channel_stats",
    "weibo": "mcp_weibo_get_profile",
    "bilibili": "mcp_bilibili_get_user_info",
}

_PLATFORM_BY_APIFY_ACTOR: Dict[str, str] = {v: k for k, v in APIFY_TOOL_PLATFORM.items()}


def _post_counts_by_platform(task_id: str) -> Dict[str, int]:
    rows = db.fetch_all(
        "SELECT platform, COUNT(*) AS c FROM collect_posts WHERE task_id=%s GROUP BY platform",
        (task_id,),
    )
    return {str(r["platform"]): int(r["c"]) for r in rows}


def _profile_counts_by_platform(task_id: str) -> Dict[str, int]:
    rows = db.fetch_all(
        "SELECT platform, COUNT(*) AS c FROM collect_profiles WHERE task_id=%s GROUP BY platform",
        (task_id,),
    )
    return {str(r["platform"]): int(r["c"]) for r in rows}


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


def _mcp_profile_terminal_fail(task_id: str, platform: str) -> bool:
    """MCP 主页工具已失败且无一成功 → 视为采集终态失败（避免 running 永久卡住父步骤）。"""
    tool = PROFILE_TOOL_BY_PLATFORM.get(platform)
    if not tool:
        return False
    err = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s AND status='error'
        """,
        (task_id, tool),
    )
    ok = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s AND status='success'
        """,
        (task_id, tool),
    )
    return int((err or {}).get("c") or 0) > 0 and int((ok or {}).get("c") or 0) == 0


def _apify_actor_success(task_id: str, platform: str) -> bool:
    tool = _PLATFORM_BY_APIFY_ACTOR.get(platform)
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


def _dataset_success_for_platform(task_id: str, platform: str) -> bool:
    """该平台是否已有成功的 get_dataset_items（phase 或 datasetId 反查）。"""
    prof_key = profile_platform_step_key(platform)
    post_key = f"step3_post_{platform}"
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items'
          AND status='success' AND phase IN (%s, %s)
        """,
        (task_id, prof_key, post_key),
    )
    if int((row or {}).get("c") or 0) > 0:
        return True

    # 历史任务可能 phase 标错（全写成 telegram），按 datasetId 反查 actor 平台
    import json

    from collect_01.normalizers.apify import resolve_apify_platform_hint

    actor_tool = _PLATFORM_BY_APIFY_ACTOR.get(platform) or ""
    expected_hint = actor_tool.replace("mcp_apify_", "")
    if not expected_hint:
        return False
    rows = db.fetch_all(
        """
        SELECT id, tool_args FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items' AND status='success'
        """,
        (task_id,),
    )
    for r in rows:
        args = json.loads(r.get("tool_args") or "{}")
        ds = str(args.get("datasetId") or args.get("dataset_id") or "").strip()
        hint = resolve_apify_platform_hint(task_id, r["id"], dataset_id=ds or None)
        if hint == expected_hint:
            return True
    return False


def _has_profile_collect_attempt(task_id: str, platform: str) -> bool:
    """该平台是否发起过正式主页采集（MCP / Apify Actor），不含 web_extract/browser。"""
    tools: list = []
    mcp = PROFILE_TOOL_BY_PLATFORM.get(platform)
    if mcp:
        tools.append(mcp)
    apify = _PLATFORM_BY_APIFY_ACTOR.get(platform)
    if apify:
        tools.append(apify)
    if not tools:
        return False
    placeholders = ",".join(["%s"] * len(tools))
    row = db.fetch_one(
        f"""
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name IN ({placeholders})
        """,
        (task_id, *tools),
    )
    return int((row or {}).get("c") or 0) > 0


def _has_post_collect_attempt(task_id: str, platform: str) -> bool:
    """该平台是否发起过正式发文采集（MCP 发文 / Apify Actor / dataset）。"""
    tools: list = []
    mcp = POST_TOOL_BY_PLATFORM.get(platform)
    if mcp:
        tools.append(mcp)
    # weibo 还有 get_user_feeds
    if platform == "weibo":
        tools.append("mcp_weibo_get_user_feeds")
    apify = _PLATFORM_BY_APIFY_ACTOR.get(platform)
    if apify:
        tools.append(apify)
    if tools:
        placeholders = ",".join(["%s"] * len(tools))
        row = db.fetch_one(
            f"""
            SELECT COUNT(*) AS c FROM hermes_tool_outputs
            WHERE task_id=%s AND tool_name IN ({placeholders})
            """,
            (task_id, *tools),
        )
        if int((row or {}).get("c") or 0) > 0:
            return True
    return _dataset_success_for_platform(task_id, platform)


def _web_bypass_for_platform(task_id: str, platform: str) -> bool:
    """Agent 是否用 web_search/web_extract/browser 等绕行该平台（非 MCP/Apify）。"""
    hints = {
        "youtube": ("youtube.com", "youtu.be"),
        "twitter": ("twitter.com", "x.com"),
        "facebook": ("facebook.com", "fb.com"),
        "instagram": ("instagram.com",),
        "telegram": ("t.me/", "telegram.me", "telegram.org"),
    }.get(platform, ())
    if not hints:
        return False
    rows = db.fetch_all(
        """
        SELECT tool_args FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name IN (
          'web_search', 'web_extract',
          'browser_navigate', 'browser_snapshot', 'browser_console',
          'browser_get_images', 'browser_click', 'browser_type', 'browser_back',
          'mcp_firecrawl_firecrawl_search', 'mcp_firecrawl_firecrawl_scrape'
        )
        """,
        (task_id,),
    )
    for row in rows:
        blob = str(row.get("tool_args") or "").lower()
        if any(h in blob for h in hints):
            return True
    return False


def _all_other_children_terminal(task_id: str, skip_step_key: str) -> bool:
    """除 skip_step_key 外，其余 step3 子节点是否均已终态。"""
    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    for row in rows:
        sk = str(row.get("step_key") or "")
        if sk == skip_step_key:
            continue
        st = str(row.get("status") or "pending")
        if st in {"pending", "running"}:
            return False
    return True


def auto_skip_unattempted_collect_children(store: Any, task_id: str, *, aggressive: bool = False) -> int:
    """
    核心防卡死：步骤一预建的平台子节点若从未调用正式采集工具，会永远 pending，
    从而堵住父步骤 step3_profiles。

    aggressive=False：仅当同批其它平台子节点均已终态，或检测到 web/浏览器绕行时 skip。
    aggressive=True：风格/Vision/会话收口时，对所有未开跑的 pending 直接 skip。
    """
    children = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    if not children:
        return 0
    updated = 0
    for row in children:
        step_key = str(row.get("step_key") or "")
        cur = str(row.get("status") or "pending")
        if cur != "pending":
            continue
        if step_key.startswith("step3_profile_"):
            platform = step_key.replace("step3_profile_", "", 1)
            if _has_profile_collect_attempt(task_id, platform):
                continue
            web_bypass = _web_bypass_for_platform(task_id, platform)
            if not aggressive and not web_bypass and not _all_other_children_terminal(task_id, step_key):
                continue
            msg = (
                f"{platform} Agent 使用 web/浏览器绕行且未走 MCP，已自动跳过"
                if web_bypass
                else f"{platform} 未发起 MCP/Apify 主页采集，已自动跳过"
            )
            store.set_step_status(task_id, step_key, "skipped", message=msg)
            updated += 1
            continue
        if step_key.startswith("step3_post_"):
            platform = step_key.replace("step3_post_", "", 1)
            if _has_post_collect_attempt(task_id, platform):
                continue
            prof_key = profile_platform_step_key(platform)
            prof_st = get_step_status(task_id, prof_key) or "pending"
            web_bypass = _web_bypass_for_platform(task_id, platform)
            if not aggressive and not web_bypass and not _all_other_children_terminal(task_id, step_key):
                continue
            if prof_st in {"failed", "skipped"}:
                msg = f"{platform} 主页未成功，跳过发文"
            elif web_bypass:
                msg = f"{platform} Agent 使用 web/浏览器绕行且未走 MCP 发文，已自动跳过"
            else:
                msg = f"{platform} 未发起 MCP/Apify 发文采集，已自动跳过"
            store.set_step_status(task_id, step_key, "skipped", message=msg)
            updated += 1
    if updated:
        logger.info(
            "自动跳过未发起采集的平台子步骤 task=%s count=%d aggressive=%s",
            task_id,
            updated,
            aggressive,
        )
    return updated


def _reconcile_vision_streams_from_tools(store: Any, task_id: str) -> int:
    """将已成功 vision 工具回写到仍 pending 的图片流（修复提前调 vision 未落库）。"""
    import json

    rows = db.fetch_all(
        """
        SELECT tool_name, tool_args, status FROM hermes_tool_outputs
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
        if not isinstance(args, dict):
            continue
        tool_name = str(row.get("tool_name") or "vision_analyze")
        if store.mark_image_stream_progress(task_id, tool_name, args, success=True):
            n += 1
    return n


def reconcile_step3_collect_child_steps(store: Any, task_id: str) -> int:
    """按 collect_profiles / collect_posts 事实校正 step3 平台子步骤。"""
    post_counts = _post_counts_by_platform(task_id)
    prof_counts = _profile_counts_by_platform(task_id)
    children = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    if not children:
        return 0

    s3_profiles = get_step_status(task_id, "step3_profiles") or "pending"
    profiles_done = s3_profiles == "completed"
    streams_done = get_step_status(task_id, "step3_streams") == "completed"
    updated = 0
    for row in children:
        step_key = str(row.get("step_key") or "")
        cur = str(row.get("status") or "pending")
        if step_key.startswith("step3_profile_"):
            platform = step_key.replace("step3_profile_", "", 1)
            cnt = prof_counts.get(platform, 0)
            if cnt > 0:
                if cur != "completed":
                    store.set_step_status(
                        task_id,
                        step_key,
                        "completed",
                        message=f"已入库主页 {cnt} 条",
                        payload={"profile_count": cnt},
                    )
                    updated += 1
                continue
            if cur == "completed":
                continue
            if cur in {"failed", "skipped", "running", "pending"}:
                if _mcp_profile_terminal_fail(task_id, platform):
                    if cur not in {"failed", "skipped"}:
                        store.set_step_status(
                            task_id,
                            step_key,
                            "failed",
                            message=f"{platform} 主页工具失败且未入库（如 YouTube 需 channelId）",
                        )
                        updated += 1
                elif _apify_actor_success(task_id, platform):
                    if _dataset_success_for_platform(task_id, platform):
                        if cur != "failed":
                            store.set_step_status(
                                task_id,
                                step_key,
                                "failed",
                                message=f"{platform} Apify 已拉取 dataset 但未入库主页",
                            )
                            updated += 1
                    elif profiles_done or streams_done:
                        if cur != "failed":
                            store.set_step_status(
                                task_id,
                                step_key,
                                "failed",
                                message=f"{platform} Actor 已完成但未拉取 dataset",
                            )
                            updated += 1
                elif streams_done and cur in {"running", "pending"}:
                    # 风格归纳已完成却仍卡 running：强制终态，解开父步骤闭环
                    if cur != "failed":
                        store.set_step_status(
                            task_id,
                            step_key,
                            "failed",
                            message=f"{platform} 主页采集未完成（已进入风格归纳）",
                        )
                        updated += 1
                elif profiles_done and cur in {"running", "pending"}:
                    if cur != "failed":
                        store.set_step_status(
                            task_id,
                            step_key,
                            "failed",
                            message=f"{platform} 主页采集未完成",
                        )
                        updated += 1
            continue
        if not step_key.startswith("step3_post_"):
            continue
        platform = step_key.replace("step3_post_", "", 1)
        prof_key = profile_platform_step_key(platform)
        prof_status = get_step_status(task_id, prof_key) or "pending"
        cnt = post_counts.get(platform, 0)
        if cnt > 0:
            if cur != "completed":
                store.set_step_status(
                    task_id,
                    step_key,
                    "completed",
                    message=f"已入库发文 {cnt} 条",
                    payload={"post_count": cnt},
                )
                updated += 1
            continue
        if cur == "completed":
            continue
        # 主页已失败/跳过 → 发文不再空等
        if prof_status in {"failed", "skipped"} and cur in {"pending", "running"}:
            store.set_step_status(
                task_id,
                step_key,
                "skipped",
                message=f"{platform} 主页未成功，跳过发文",
            )
            updated += 1
            continue
        if cnt == 0 and cur in {"running", "pending"} and _dataset_success_for_platform(task_id, platform):
            if cur != "skipped":
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"{platform} 未采集到发文",
                )
                updated += 1
            continue
        if prof_status in {"failed", "skipped"}:
            if cur != "skipped":
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"{platform} 主页采集失败，跳过发文",
                )
                updated += 1
            continue
        if profiles_done and streams_done and _post_tool_success(task_id, platform):
            if cur != "skipped":
                store.set_step_status(
                    task_id,
                    step_key,
                    "skipped",
                    message=f"{platform} 发文工具已调用但未入库",
                )
                updated += 1
    return updated


def reconcile_stuck_pipeline(store: Any, task_id: str) -> None:
    """会话结束时兜底推进未完成步骤。"""
    s1 = get_step_status(task_id, "step1_input_accounts")
    if s1 == "running":
        return

    reconcile_step3_collect_child_steps(store, task_id)
    s3p = get_step_status(task_id, "step3_profiles")
    if s3p in {"pending", "running"}:
        # 会话结束：未发起正式采集的平台子节点不再空等
        from verify_03.step_reconcile import auto_skip_unattempted_collect_children

        auto_skip_unattempted_collect_children(store, task_id, aggressive=True)
        store.reconcile_profile_platform_steps(task_id)

    # vision 回写不依赖 step3 完成
    _reconcile_vision_streams_from_tools(store, task_id)

    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        return

    s_stream = get_step_status(task_id, "step3_streams")
    if s_stream in {"pending", "running"}:
        store.set_step_status(task_id, "step3_streams", "completed", message="发文风格归纳完成")

    if get_step_status(task_id, "step4_text_compare") in {"pending", "running"}:
        try:
            store.run_text_compare(task_id)
        except Exception as exc:
            logger.warning("reconcile text_compare 失败 task=%s: %s", task_id, exc)

    s4img = get_step_status(task_id, "step4_image_compare")
    if s4img in {"pending", "running"}:
        if is_image_compare_ready(task_id):
            n_img = count_image_streams(task_id)
            msg = "无头像图片流，跳过图片比对" if n_img == 0 else "图片流比对完成"
            store.set_step_status(task_id, "step4_image_compare", "completed", message=msg)
        elif hasattr(store, "mark_remaining_image_streams_failed"):
            n_skip = store.mark_remaining_image_streams_failed(task_id, "会话结束兜底：未完成 vision")
            if n_skip or count_image_streams(task_id) == 0:
                msg = (
                    "无头像图片流，跳过图片比对"
                    if count_image_streams(task_id) == 0
                    else f"图片流比对结束（兜底跳过 {n_skip} 条）"
                )
                store.set_step_status(task_id, "step4_image_compare", "completed", message=msg)

    if (
        get_step_status(task_id, "step4_text_compare") == "completed"
        and get_step_status(task_id, "step4_image_compare") == "completed"
        and get_step_status(task_id, "step5_validated") in {"pending", "running"}
    ):
        try:
            store.run_validated_accounts(task_id)
        except Exception as exc:
            logger.warning("reconcile validated 失败 task=%s: %s", task_id, exc)
