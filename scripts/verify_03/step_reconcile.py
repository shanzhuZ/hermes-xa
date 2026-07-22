"""03 账号核查 — 步骤收口与卡死恢复。"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from collect_01 import db
from collect_01.normalizers.apify import (
    APIFY_TOOL_PLATFORM,
    apify_fail_message,
    normalize_dataset_items,
    resolve_apify_platform_hint,
)
from collect_01.normalizers.base import unwrap_tool_payload
from collect_01.normalizers.youtube import youtube_channel_id_ok
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
    """MCP 主页工具已失败且无一成功 → 视为采集终态失败（避免 running 永久卡住父步骤）。

    YouTube：仅当「合法 UC channelId」调用失败且无成功时才终态；
    @handle / 伪 UC 失败不算终态（Agent 会继续查 UC）。
    """
    tool = PROFILE_TOOL_BY_PLATFORM.get(platform)
    if not tool:
        return False
    ok = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s AND status='success'
        """,
        (task_id, tool),
    )
    if int((ok or {}).get("c") or 0) > 0:
        return False

    if platform == "youtube":
        import json

        rows = db.fetch_all(
            """
            SELECT tool_args FROM hermes_tool_outputs
            WHERE task_id=%s AND tool_name=%s AND status='error'
            ORDER BY id DESC
            """,
            (task_id, tool),
        )
        for row in rows:
            try:
                args = json.loads(row.get("tool_args") or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                continue
            cid = str(args.get("channelId") or args.get("channel_id") or "").strip()
            if youtube_channel_id_ok(cid):
                return True
        return False

    err = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s AND status='error'
        """,
        (task_id, tool),
    )
    return int((err or {}).get("c") or 0) > 0


def _latest_apify_collect_outcome(task_id: str, platform: str) -> str:
    """重解析该平台最近一次成功 dataset，得到 collect_outcome。"""
    import json

    actor_tool = _PLATFORM_BY_APIFY_ACTOR.get(platform) or ""
    expected_hint = actor_tool.replace("mcp_apify_", "")
    if not expected_hint:
        return "empty"
    rows = db.fetch_all(
        """
        SELECT id, tool_args, tool_output FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items' AND status='success'
        ORDER BY id DESC
        """,
        (task_id,),
    )
    for row in rows:
        try:
            args = json.loads(row.get("tool_args") or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        ds = str(args.get("datasetId") or args.get("dataset_id") or "").strip()
        hint = resolve_apify_platform_hint(task_id, row["id"], dataset_id=ds or None)
        if hint != expected_hint:
            continue
        raw = row.get("tool_output")
        try:
            payload = unwrap_tool_payload(raw)
        except Exception:
            payload = raw
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        out = normalize_dataset_items(
            payload if isinstance(payload, dict) else {},
            {
                "task_id": task_id,
                "tool_output_id": row["id"],
                "platform_hint": expected_hint,
            },
        )
        return str(out.get("collect_outcome") or "empty")
    return "empty"


def _apify_actor_success(task_id: str, platform: str) -> bool:
    return _apify_actor_success_count(task_id, platform) > 0


def _apify_actor_success_count(task_id: str, platform: str) -> int:
    tool = _PLATFORM_BY_APIFY_ACTOR.get(platform)
    if not tool:
        return 0
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name=%s AND status='success'
        """,
        (task_id, tool),
    )
    return int((row or {}).get("c") or 0)


def _post_round_dataset_success(task_id: str, platform: str) -> bool:
    """发文轮 get_dataset_items 已成功（phase=step3_post_*），无论是否解析出帖子。"""
    post_key = f"step3_post_{platform}"
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND tool_name='mcp_apify_get_dataset_items'
          AND status='success' AND phase=%s
        """,
        (task_id, post_key),
    )
    return int((row or {}).get("c") or 0) > 0


def _platform_collect_never_started(task_id: str, platform: str) -> bool:
    """主页+发文子步都仍是 pending：视为从未开跑（不阻塞其它平台 peer-skip）。"""
    if not platform:
        return True
    prof = get_step_status(task_id, profile_platform_step_key(platform)) or "pending"
    post = get_step_status(task_id, f"step3_post_{platform}") or "pending"
    return prof == "pending" and post == "pending"


def _all_other_started_platforms_terminal(task_id: str, platform: str) -> bool:
    """其它「已开跑」平台是否均已终态。

    关键：忽略双方都还 pending 的未开跑平台，否则会出现
    youtube(pending) ↔ facebook(等待发文 running) 互相阻塞的死锁。
    """
    if not platform:
        return False
    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    by_plat: Dict[str, Dict[str, str]] = {}
    for row in rows:
        sk = str(row.get("step_key") or "")
        p = _collect_platform_of_step(sk)
        if not p or p == platform:
            continue
        bucket = by_plat.setdefault(p, {})
        st = str(row.get("status") or "pending")
        if sk.startswith("step3_profile_"):
            bucket["profile"] = st
        elif sk.startswith("step3_post_"):
            bucket["post"] = st

    saw_started = False
    for p, stmap in by_plat.items():
        prof = stmap.get("profile") or "pending"
        post = stmap.get("post") or "pending"
        if prof == "pending" and post == "pending":
            continue
        saw_started = True
        if prof in {"pending", "running"} or post in {"pending", "running"}:
            return False
    return saw_started


def _can_skip_empty_posts(
    task_id: str,
    platform: str,
    *,
    profiles_done: bool,
    streams_done: bool,
) -> bool:
    """主页已完成、发文仍 0 条时，是否可在步骤2内 skip（禁止依赖父步骤已完成造成死锁）。

    触发条件（满足其一即可）：
    1. 发文轮 dataset 已成功（空结果当场收口）
    2. 其它已开跑平台均已终态（未开跑 pending 不阻塞），且本平台已有采集尝试
    3. 父步骤或风格阶段已完成（历史兜底）
    """
    if _post_round_dataset_success(task_id, platform):
        return True
    attempted = (
        _dataset_success_for_platform(task_id, platform)
        or _apify_actor_success_count(task_id, platform) >= 1
        or _post_tool_success(task_id, platform)
    )
    # 优先用「已开跑平台」判定，避免未采集的 youtube pending 卡住 facebook 发文
    if attempted and (
        _all_other_started_platforms_terminal(task_id, platform)
        or _all_other_platforms_terminal(task_id, platform)
    ):
        return True
    if (profiles_done or streams_done) and (
        _dataset_success_for_platform(task_id, platform) or _post_tool_success(task_id, platform)
    ):
        return True
    return False


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


def _collect_platform_of_step(step_key: str) -> str:
    if step_key.startswith("step3_profile_"):
        return step_key.replace("step3_profile_", "", 1)
    if step_key.startswith("step3_post_"):
        return step_key.replace("step3_post_", "", 1)
    return ""


def _all_other_platforms_terminal(task_id: str, platform: str) -> bool:
    """其它平台的主页/发文子节点是否均已终态。

    注意：必须按「平台」判断，不能按「单个 step_key」——
    否则 youtube 主页+发文互为 pending 会死锁，永远无法 peer-skip。
    """
    if not platform:
        return False
    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    saw_other = False
    for row in rows:
        sk = str(row.get("step_key") or "")
        p = _collect_platform_of_step(sk)
        if not p or p == platform:
            continue
        saw_other = True
        st = str(row.get("status") or "pending")
        if st in {"pending", "running"}:
            return False
    return saw_other


def auto_skip_unattempted_collect_children(store: Any, task_id: str, *, aggressive: bool = False) -> int:
    """
    核心防卡死：步骤一预建的平台子节点若从未调用正式采集工具，会永远 pending，
    从而堵住父步骤 step3_profiles。

    aggressive=False（采集过程）：仅当「其它平台均已终态」才 skip 本平台未开跑节点。
      Agent 若随后真调 MCP，sink 会从 skipped 重开为 running。
      禁止用 web_search 域名单独触发 skip。
    aggressive=True：仅会话结束硬收口。
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
    # 先主页后发文，保证发文文案能看到主页已 skip
    ordered = sorted(
        children,
        key=lambda r: (0 if str(r.get("step_key") or "").startswith("step3_profile_") else 1),
    )
    updated = 0
    for row in ordered:
        step_key = str(row.get("step_key") or "")
        cur = str(row.get("status") or "pending")
        if cur != "pending":
            continue
        if step_key.startswith("step3_profile_"):
            platform = step_key.replace("step3_profile_", "", 1)
            if _has_profile_collect_attempt(task_id, platform):
                continue
            if not aggressive and not _all_other_platforms_terminal(task_id, platform):
                continue
            web_bypass = _web_bypass_for_platform(task_id, platform)
            if aggressive:
                msg = (
                    f"{platform} 会话收口：未见 MCP/Apify 主页采集，已跳过"
                    + ("（过程中仅有 web/浏览器）" if web_bypass else "")
                )
            else:
                msg = (
                    f"{platform} 未发起 MCP/Apify 主页采集，已自动跳过"
                    + ("（过程中仅有 web/浏览器）" if web_bypass else "")
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
            # 主页已失败/跳过：发文可直接 skip，不必等其它平台
            if prof_st not in {"failed", "skipped"}:
                if not aggressive and not _all_other_platforms_terminal(task_id, platform):
                    continue
            web_bypass = _web_bypass_for_platform(task_id, platform)
            if prof_st in {"failed", "skipped"}:
                msg = f"{platform} 主页未成功，跳过发文"
            elif web_bypass:
                msg = f"{platform} 未发起 MCP/Apify 发文采集，已自动跳过（过程中仅有 web/浏览器）"
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
                    # 若曾被误 skip，先重开再完成，刷新 finished_at
                    if cur in {"skipped", "failed"}:
                        store.set_step_status(
                            task_id,
                            step_key,
                            "running",
                            message=f"{platform} 主页采集结果回写中…",
                        )
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
                    # YouTube：仅合法 UC 失败才进此分支；文案勿再误导「需 channelId」
                    if cur not in {"failed", "skipped"}:
                        store.set_step_status(
                            task_id,
                            step_key,
                            "failed",
                            message=f"{platform} 主页工具失败且未入库",
                        )
                        updated += 1
                elif _apify_actor_success(task_id, platform):
                    if _dataset_success_for_platform(task_id, platform):
                        outcome = _latest_apify_collect_outcome(task_id, platform)
                        msg = apify_fail_message(platform, outcome)
                        # 校正误导性「未入库」文案（含已 failed 的历史脏状态）
                        if cur != "skipped":
                            store.set_step_status(
                                task_id,
                                step_key,
                                "failed",
                                message=msg,
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
                elif (
                    cur == "running"
                    and platform == "youtube"
                    and _all_other_platforms_terminal(task_id, platform)
                    and not _mcp_profile_terminal_fail(task_id, platform)
                ):
                    # 其它平台已完，仍卡在「等待合法 UC」→ 跳过（MCP 真调用可 reopen）
                    store.set_step_status(
                        task_id,
                        step_key,
                        "skipped",
                        message="youtube 未完成合法 channelId 采集，已跳过",
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
                if cur in {"skipped", "failed"}:
                    store.set_step_status(
                        task_id,
                        step_key,
                        "running",
                        message=f"{platform} 发文采集结果回写中…",
                    )
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
        # 主页已完成、发文 0 条：步骤2内 peer/发文轮收口（禁止等父步骤 completed 造成死锁）
        if (
            cnt == 0
            and cur in {"running", "pending"}
            and prof_status == "completed"
            and _can_skip_empty_posts(
                task_id,
                platform,
                profiles_done=profiles_done,
                streams_done=streams_done,
            )
        ):
            if cur != "skipped":
                msg = (
                    f"{platform} 未采集到发文"
                    if _post_round_dataset_success(task_id, platform)
                    or profiles_done
                    or streams_done
                    else f"{platform} 未采集到发文（其它平台已收口）"
                )
                store.set_step_status(task_id, step_key, "skipped", message=msg)
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


def force_close_open_collect_children(store: Any, task_id: str) -> int:
    """会话结束：仍卡在 pending/running 的主页/发文子步骤强制终态。

    覆盖场景：YouTube 仅错误传 @handle/伪 UC，中途保持 running，会话结束仍无合法 UC 成功。
    """
    post_counts = _post_counts_by_platform(task_id)
    prof_counts = _profile_counts_by_platform(task_id)
    children = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key=%s
        """,
        (task_id, PROFILE_PARENT_STEP_KEY),
    )
    updated = 0
    # 先收口主页，再收口发文（发文依赖主页终态文案）
    ordered = sorted(
        children,
        key=lambda r: (0 if str(r.get("step_key") or "").startswith("step3_profile_") else 1),
    )
    for row in ordered:
        step_key = str(row.get("step_key") or "")
        cur = str(row.get("status") or "pending")
        if cur not in {"pending", "running"}:
            continue
        if step_key.startswith("step3_profile_"):
            platform = step_key.replace("step3_profile_", "", 1)
            if prof_counts.get(platform, 0) > 0:
                continue
            store.set_step_status(
                task_id,
                step_key,
                "failed",
                message=f"{platform} 会话结束：主页采集未完成",
            )
            updated += 1
            continue
        if step_key.startswith("step3_post_"):
            platform = step_key.replace("step3_post_", "", 1)
            if post_counts.get(platform, 0) > 0:
                continue
            prof_st = get_step_status(task_id, profile_platform_step_key(platform)) or "pending"
            if prof_st in {"failed", "skipped"}:
                msg = f"{platform} 主页未成功，跳过发文"
            else:
                msg = f"{platform} 会话结束：未采集到发文"
            store.set_step_status(task_id, step_key, "skipped", message=msg)
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
        auto_skip_unattempted_collect_children(store, task_id, aggressive=True)
        force_close_open_collect_children(store, task_id)
        store.reconcile_profile_platform_steps(task_id)

    # vision 回写不依赖 step3 完成
    _reconcile_vision_streams_from_tools(store, task_id)

    # 先收口风格步骤，再查 step4 门禁（避免 step3_streams=running 时永远 return）
    s3p_now = get_step_status(task_id, "step3_profiles")
    s_stream = get_step_status(task_id, "step3_streams")
    if s3p_now in {"completed", "skipped"} and s_stream in {"pending", "running"}:
        store.set_step_status(task_id, "step3_streams", "completed", message="发文风格归纳完成")

    # 若已有终稿，直接走终稿收口（含 step4/5 + task completed）
    summary = db.fetch_one(
        "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
        (task_id,),
    )
    if summary and hasattr(store, "close_analysis_after_report"):
        try:
            store.close_analysis_after_report(task_id)
            return
        except Exception as exc:
            logger.warning("reconcile 终稿收口失败 task=%s: %s", task_id, exc)

    gate = can_advance_to_step45(task_id)
    if not gate.get("ok"):
        return

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
