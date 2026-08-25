"""04：平台发文完成后短触发后台视频分析（防 Hook 超时）。

节点：step7_post_{platform} → step7_video_{platform}（5.1.x.1）。
无可用视频不建节点；有则后台跑。
方案 A：发文子步入库即可 completed（不挡进分析）；
step7_posts / phase_content 须等全部 step7_video_* 终态再收口。

空 pending 自愈：
- 父壳未关且未进研判 → 补启 runner（可点亮父壳，符合「仍在等视频」）
- 父壳已关或已进研判 → 直接 skipped，禁止回开流程图
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from collect_01 import db
from common.source_tag import source_tag_json
from collect_01.video_job import (
    _isolated_video_env,
    _scripts_dir,
    _video_python,
)
from collect_01.video_select import select_latest_downloadable_video
from report_04.gates import get_step_status
from report_04.phases import (
    ANALYSIS_STEP_KEYS,
    PHASE_ANALYSIS_SHELL,
    POST_PARENT_STEP_KEY,
    post_platform_step_key,
    post_step_order,
    post_step_node,
    post_step_title,
    video_step_key,
    video_step_node,
    video_step_order,
    video_step_title,
)

logger = logging.getLogger(__name__)

# 分析参数：只取前 120 秒（2 分钟）、3 秒一帧（runner 内再传）
VIDEO_MAX_DURATION_SEC = int(os.environ.get("HERMES_REPORT_VIDEO_MAX_DURATION_SEC", "120"))
VIDEO_FRAME_INTERVAL_SEC = float(os.environ.get("HERMES_REPORT_VIDEO_FRAME_INTERVAL_SEC", "3"))
# 墙钟默认 10 分钟
VIDEO_WALL_TIMEOUT_SEC = int(os.environ.get("HERMES_REPORT_VIDEO_TIMEOUT_SEC", "600"))
# 轮询侧空 pending 宽限：避免与刚 INSERT、即将 set running 的并发竞态
VIDEO_ZOMBIE_PENDING_GRACE_SEC = int(
    os.environ.get("HERMES_REPORT_VIDEO_ZOMBIE_PENDING_GRACE_SEC", "30")
)


def fetch_video_step_row(task_id: str, platform: str) -> Optional[Dict[str, Any]]:
    key = video_step_key(platform)
    row = db.fetch_one(
        """
        SELECT step_key, status, started_at, created_at, updated_at, payload_json, message
        FROM collect_phase_steps
        WHERE task_id=%s AND step_key=%s
        """,
        (task_id, key),
    )
    return dict(row) if row else None


def is_zombie_pending_video_row(row: Optional[Dict[str, Any]]) -> bool:
    """空 pending：已建节点但从未 running（started_at 为空），常见于 ensure 后中断。"""
    if not row:
        return False
    if str(row.get("status") or "") != "pending":
        return False
    return row.get("started_at") is None


def video_respawn_allowed(task_id: str) -> bool:
    """仅当发文父壳未收口、且深度研判尚未点亮时允许补启（避免完成后回流）。"""
    s7 = get_step_status(task_id, POST_PARENT_STEP_KEY)
    if s7 in {"completed", "skipped"}:
        return False
    for k in (PHASE_ANALYSIS_SHELL,) + tuple(ANALYSIS_STEP_KEYS) + ("step11_report",):
        st = get_step_status(task_id, k)
        if st in {"running", "completed", "skipped", "failed"}:
            return False
    return True


def skip_zombie_pending_video(
    store: Any,
    task_id: str,
    platform: str,
    *,
    message: str,
) -> None:
    """晚到空 pending：跳过且不滚父壳，避免发文采集 / 内容壳被打回 running。"""
    platform = str(platform or "").strip().lower()
    if not platform:
        return
    key = video_step_key(platform)
    store.set_step_status(
        task_id,
        key,
        "skipped",
        message=(message or "空 pending 未拉起，跳过")[:2000],
        payload={"zombie_pending_skipped": True},
        skip_phase_rollup=True,
    )
    try:
        complete_post_after_video(store, task_id, platform)
    except Exception as exc:
        logger.warning(
            "04 空 pending skip 后收口失败 task=%s platform=%s: %s",
            task_id,
            platform,
            exc,
        )


def ensure_video_step(store: Any, task_id: str, platform: str) -> str:
    """确保 5.1.x.1 存在，父节点为 step7_post_{platform}。"""
    parent = post_platform_step_key(platform)
    if hasattr(store, "ensure_post_steps"):
        store.ensure_post_steps(task_id, [platform])
    else:
        db.execute(
            """
            INSERT IGNORE INTO collect_phase_steps
              (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
            """,
            (
                task_id,
                parent,
                POST_PARENT_STEP_KEY,
                post_step_order(platform),
                post_step_node(platform),
                post_step_title(platform),
                source_tag_json(parent),
            ),
        )
    key = video_step_key(platform)
    db.execute(
        """
        INSERT IGNORE INTO collect_phase_steps
          (task_id, step_key, parent_step_key, step_order, step_node, title, source_tag, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
        """,
        (
            task_id,
            key,
            parent,
            video_step_order(platform),
            video_step_node(platform),
            video_step_title(platform),
            source_tag_json(key),
        ),
    )
    return key


def maybe_start_platform_video(store: Any, task_id: str, platform: str) -> Optional[Dict[str, Any]]:
    """发文入库后：有可下载视频则建 5.1.x.1 并后台跑；无则不建。数秒内返回。

    空 pending：父壳未关且未进研判则补启；否则 skip（不回开流程图）。
    """
    if not task_id or not platform:
        return None
    platform = str(platform).strip().lower()
    key = video_step_key(platform)
    existing = get_step_status(task_id, key)
    row = fetch_video_step_row(task_id, platform)

    if existing == "running":
        logger.info("04 视频节点已在跑 task=%s platform=%s", task_id, platform)
        return {"skipped": True, "reason": "already_exists", "step_key": key, "status": existing}
    if existing in {"completed", "skipped"}:
        logger.info(
            "04 视频节点已终态 task=%s platform=%s status=%s", task_id, platform, existing
        )
        return {"skipped": True, "reason": "already_exists", "step_key": key, "status": existing}

    if existing == "pending":
        if is_zombie_pending_video_row(row):
            if not video_respawn_allowed(task_id):
                logger.info(
                    "04 空 pending 已晚（父壳已关/已进研判），跳过补启 task=%s platform=%s",
                    task_id,
                    platform,
                )
                skip_zombie_pending_video(
                    store,
                    task_id,
                    platform,
                    message="空 pending 未拉起且发文父壳已收口或已进研判，跳过以免回开流程图",
                )
                return {
                    "skipped": True,
                    "reason": "zombie_late_skip",
                    "step_key": key,
                    "status": "skipped",
                }
            logger.info("04 空 pending 补启 task=%s platform=%s", task_id, platform)
            # 落入下方选片 + set running + spawn
        else:
            # 非空 pending（极少）：仍视为进行中，不重复拉起
            logger.info(
                "04 视频节点 pending（非空）task=%s platform=%s", task_id, platform
            )
            return {
                "skipped": True,
                "reason": "already_exists",
                "step_key": key,
                "status": existing,
            }
    elif existing == "failed":
        logger.info("04 视频节点曾失败，准备重试 task=%s platform=%s", task_id, platform)

    picked = select_latest_downloadable_video(task_id, platform)
    if not picked or not picked.get("url"):
        logger.info("04 平台无下载视频，不建节点 task=%s platform=%s", task_id, platform)
        # 已有空 pending 却选不出片：收口掉，避免永久挡父壳
        if existing == "pending" and is_zombie_pending_video_row(row):
            skip_zombie_pending_video(
                store,
                task_id,
                platform,
                message="空 pending 补启时无可用下载视频，跳过",
            )
            return {
                "skipped": True,
                "reason": "no_video",
                "step_key": key,
                "status": "skipped",
            }
        return {"skipped": True, "reason": "no_video"}

    ensure_video_step(store, task_id, platform)
    store.set_step_status(
        task_id,
        key,
        "running",
        message=f"后台分析最新视频 {picked.get('post_id') or ''}".strip(),
        payload={
            "origin_url": picked["url"],
            "post_id": picked.get("post_id"),
            "account_id": picked.get("account_id"),
            "wall_timeout_sec": VIDEO_WALL_TIMEOUT_SEC,
            "max_duration_sec": VIDEO_MAX_DURATION_SEC,
            "frame_interval_sec": VIDEO_FRAME_INTERVAL_SEC,
        },
    )
    spawned = _spawn_video_runner(
        task_id=task_id,
        platform=platform,
        origin_url=str(picked["url"]),
        account_id=str(picked.get("account_id") or ""),
        post_id=str(picked.get("post_id") or ""),
    )
    logger.info(
        "04 已拉起视频后台 task=%s platform=%s url=%s pid=%s",
        task_id,
        platform,
        picked["url"][:120],
        spawned.get("pid"),
    )
    return {"started": True, "step_key": key, "pick": picked, **spawned}


def finalize_post_platform_after_posts(
    store: Any,
    task_id: str,
    platform: str,
    *,
    post_count: int,
    force_reopen: bool = False,
) -> str:
    """发文已入库后的统一收口：尝试拉起视频；工具返回前不得 completed。

    有视频时：发文子步 completed（旁路，不挡进分析）；
    step7_posts / phase_content 由收口逻辑等视频孙节点终态。

    返回最终发文子步状态：completed / running（在飞时）/ pending。
    """
    platform = str(platform or "").strip().lower()
    if not platform or post_count <= 0:
        return get_step_status(task_id, post_platform_step_key(platform)) or "pending"

    post_key = post_platform_step_key(platform)

    # 发文 MCP 仍在飞：保持 running，禁止 reconcile 边入边完
    from report_04.gates import is_post_tool_inflight

    if is_post_tool_inflight(task_id, platform):
        cur = get_step_status(task_id, post_key) or "pending"
        if cur != "running":
            store.set_step_status(
                task_id,
                post_key,
                "running",
                message=f"{platform} 发文工具执行中（已入库 {post_count} 条，待工具返回）",
                payload={"post_count": post_count, "await_post_tool": True},
                force_reopen=(cur in {"completed", "failed", "skipped"}),
            )
        return "running"

    video_key = video_step_key(platform)
    v_st = get_step_status(task_id, video_key)

    # 视频真正在跑：发文子步旁路 completed（父壳另等视频终态）
    if v_st == "running":
        store.set_step_status(
            task_id,
            post_key,
            "completed",
            message=f"已入库发文 {post_count} 条（视频分析后台进行中）",
            payload={"post_count": post_count, "await_video": True},
            force_reopen=True,
        )
        return "completed"

    # 空 pending：立即尝试补启 / 晚到 skip（不再把 pending 当「进行中」永久空等）
    if v_st == "pending":
        try:
            result = maybe_start_platform_video(store, task_id, platform) or {}
        except Exception as exc:
            logger.warning(
                "04 pending 视频补启失败 task=%s platform=%s: %s", task_id, platform, exc
            )
            result = {"skipped": True, "reason": "start_error", "error": str(exc)}
        v_st2 = get_step_status(task_id, video_key)
        if result.get("started") or v_st2 == "running":
            store.set_step_status(
                task_id,
                post_key,
                "completed",
                message=f"已入库发文 {post_count} 条（视频分析后台进行中）",
                payload={"post_count": post_count, "await_video": True},
                force_reopen=True,
            )
            return "completed"
        if v_st2 in {"completed", "failed", "skipped"}:
            store.set_step_status(
                task_id,
                post_key,
                "completed",
                message=f"已入库发文 {post_count} 条（视频 {v_st2}）",
                payload={"post_count": post_count, "video_status": v_st2},
                force_reopen=force_reopen,
            )
            return "completed"
        # 仍 pending（宽限内极少）：先旁路完成发文子步，父壳继续等轮询自愈
        store.set_step_status(
            task_id,
            post_key,
            "completed",
            message=f"已入库发文 {post_count} 条（视频分析后台进行中）",
            payload={"post_count": post_count, "await_video": True},
            force_reopen=True,
        )
        return "completed"

    if v_st in {"completed", "failed", "skipped"}:
        store.set_step_status(
            task_id,
            post_key,
            "completed",
            message=f"已入库发文 {post_count} 条（视频 {v_st}）",
            payload={"post_count": post_count, "video_status": v_st},
            force_reopen=force_reopen,
        )
        return "completed"

    # 尚无视频节点：尝试拉起
    try:
        result = maybe_start_platform_video(store, task_id, platform) or {}
    except Exception as exc:
        logger.warning("04 拉起视频失败 task=%s platform=%s: %s", task_id, platform, exc)
        result = {"skipped": True, "reason": "start_error", "error": str(exc)}

    if (
        result.get("started")
        or result.get("status") == "running"
        or (
            result.get("reason") == "already_exists"
            and result.get("status") == "running"
        )
    ):
        # 视频旁路：不挡发文子步 completed / 进分析；挡 step7_posts 与 phase_content
        store.set_step_status(
            task_id,
            post_key,
            "completed",
            message=f"已入库发文 {post_count} 条（视频分析后台进行中）",
            payload={"post_count": post_count, "await_video": True},
            force_reopen=force_reopen,
        )
        return "completed"

    # no_video / zombie_late_skip / 其它：直接完成发文子步
    v_final = get_step_status(task_id, video_key)
    if v_final in {"completed", "failed", "skipped"}:
        store.set_step_status(
            task_id,
            post_key,
            "completed",
            message=f"已入库发文 {post_count} 条（视频 {v_final}）",
            payload={"post_count": post_count, "video_status": v_final},
            force_reopen=force_reopen,
        )
        return "completed"

    store.set_step_status(
        task_id,
        post_key,
        "completed",
        message=f"已入库发文 {post_count} 条",
        payload={"post_count": post_count},
        force_reopen=force_reopen,
    )
    return "completed"


def complete_post_after_video(store: Any, task_id: str, platform: str) -> None:
    """视频节点终态后：补收口仍未终态的发文子步，并尝试关 step7_posts / phase_content。"""
    platform = str(platform or "").strip().lower()
    if not platform:
        return
    post_key = post_platform_step_key(platform)
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s AND platform=%s",
        (task_id, platform),
    )
    cnt = int((row or {}).get("c") or 0)
    v_st = get_step_status(task_id, video_step_key(platform))
    if v_st not in {"completed", "failed", "skipped"}:
        return
    if cnt <= 0:
        return
    cur = get_step_status(task_id, post_key)
    if cur not in {"completed", "skipped", "failed"}:
        store.set_step_status(
            task_id,
            post_key,
            "completed",
            message=f"已入库发文 {cnt} 条（视频 {v_st}）",
            payload={"post_count": cnt, "video_status": v_st},
        )
    try:
        from report_04.step_reconcile import close_collect_parent_if_ready

        close_collect_parent_if_ready(
            store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕"
        )
        if hasattr(store, "_maybe_complete_phase_shell"):
            store._maybe_complete_phase_shell(task_id, POST_PARENT_STEP_KEY)
    except Exception as exc:
        logger.warning("04 视频后关 step7 父节点失败 task=%s: %s", task_id, exc)

    # 视频终态后催续跑：门禁未齐（其它视频仍跑）时会走 hold，全部齐后才 analysis
    try:
        from report_04.session_continue import (
            flush_deferred_continue,
            maybe_continue_agent_session,
        )

        maybe_continue_agent_session(
            store,
            task_id,
            reason=f"video_terminal:{platform}:{v_st}",
        )
        flush_deferred_continue(store, task_id, trigger=f"video_{platform}")
    except Exception as exc:
        logger.warning("04 视频终态催分析续跑失败 task=%s: %s", task_id, exc)


def _spawn_video_runner(
    *,
    task_id: str,
    platform: str,
    origin_url: str,
    account_id: str,
    post_id: str,
) -> Dict[str, Any]:
    py = _video_python()
    scripts = _scripts_dir()
    log_dir = Path(os.environ.get("HERMES_HOME") or scripts.parent) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"report_video_{task_id[:8]}_{platform}.log"

    cmd = [
        py,
        "-m",
        "report_04.video_runner",
        "--task-id",
        task_id,
        "--platform",
        platform,
        "--origin-url",
        origin_url,
        "--account-id",
        account_id,
        "--post-id",
        post_id,
        "--timeout-sec",
        str(VIDEO_WALL_TIMEOUT_SEC),
        "--max-duration-sec",
        str(VIDEO_MAX_DURATION_SEC),
        "--frame-interval-sec",
        str(VIDEO_FRAME_INTERVAL_SEC),
    ]
    env = _isolated_video_env(scripts)
    logger.info("04 视频后台 python=%s PYTHONPATH=%s", py, env.get("PYTHONPATH"))

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(scripts),
        "env": env,
        "stdout": open(log_path, "a", encoding="utf-8"),
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        popen_kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        popen_kwargs["close_fds"] = False
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    return {"pid": proc.pid, "log": str(log_path)}
