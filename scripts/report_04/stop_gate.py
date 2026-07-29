# -*- coding: utf-8 -*-
"""04 写报 · 主进程 Stop 门闩。

防止主进程在「四、关联碰撞」阶段（及碰撞刚齐、发文尚未落地时）因复述步骤4而 stop。
不改续跑逻辑；仅在 pre_verify 返回 continue 文案，由 Hermes 同会话再开一轮。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from report_04.gates import get_step_status

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"completed", "failed", "skipped"})

# 碰撞相关根步（任一未终态 → 仍处「四、关联碰撞」）
_COLLISION_STEPS = (
    "step5_streams",
    "step6_validated",
    "step6_osint_es",
)

# collision=碰撞未齐；posts=碰撞已齐但发文未落地（防滞后「步骤4完成」收束）
_REASON_COLLISION = "collision"
_REASON_POSTS = "posts"


def collision_phase_incomplete(task_id: str) -> bool:
    """四、关联碰撞是否尚未全部终态。"""
    for sk in _COLLISION_STEPS:
        st = get_step_status(task_id, sk)
        if st not in _TERMINAL:
            return True
    return False


def _posts_still_due(task_id: str) -> bool:
    """碰撞已齐后：发文门禁开且仍有待采 / 无帖 → 视为发文未落地。"""
    try:
        from report_04.gates import can_run_step7_collect, posts_substantively_ready

        if posts_substantively_ready(task_id):
            return False
        if not can_run_step7_collect(task_id):
            return False
        from report_04.session_continue import post_count
        from report_04.step_reconcile import list_unattempted_post_platforms

        todo = list_unattempted_post_platforms(task_id) or []
        if todo:
            return True
        return post_count(task_id) <= 0
    except Exception as exc:
        logger.warning("stop_gate 发文待采检查失败 task=%s: %s", task_id, exc)
        return False


def classify_stop_block(task_id: str) -> Optional[str]:
    """返回拦截原因 collision|posts；None 表示允许 stop。"""
    if not task_id:
        return None
    try:
        from report_04.session_continue import has_final_report

        if has_final_report(task_id):
            return None
    except Exception:
        if get_step_status(task_id, "step11_report") == "completed":
            return None

    s4 = get_step_status(task_id, "step4_profiles")
    if s4 not in _TERMINAL:
        return None

    if collision_phase_incomplete(task_id):
        return _REASON_COLLISION

    # 碰撞已齐：禁止以「步骤4完成」收束离开主进程，须先落地发文
    if _posts_still_due(task_id):
        return _REASON_POSTS

    return None


def should_block_report_stop(task_id: str) -> bool:
    """步骤3已终态后，碰撞未齐或发文未落地 → 禁止主进程 stop。"""
    return classify_stop_block(task_id) is not None


def _collision_status_line(task_id: str) -> str:
    parts = []
    for sk in _COLLISION_STEPS:
        parts.append(f"{sk}={get_step_status(task_id, sk) or 'pending'}")
    return "; ".join(parts)


def _posts_todo_hint(task_id: str) -> str:
    try:
        from report_04.step_reconcile import list_unattempted_post_platforms

        todo = list_unattempted_post_platforms(task_id) or []
        if not todo:
            return "请对全部 validated 调用发文工具"
        hints = "; ".join(
            f"{x.get('platform')}:{x.get('tool_hint')}" for x in todo[:6]
        )
        return f"尚未尝试 {len(todo)} 个：{hints}"
    except Exception:
        return "请对全部 validated 调用发文工具"


def build_stop_gate_message(
    task_id: str,
    *,
    attempt: int = 0,
    reason: Optional[str] = None,
) -> str:
    """合成 user 催促文案（注入主会话，禁止 stop）。"""
    n = int(attempt) + 1
    why = reason or classify_stop_block(task_id) or _REASON_COLLISION
    if why == _REASON_POSTS:
        return (
            "【系统·禁止结束会话】四、关联碰撞已收口，发文门禁已开。"
            "禁止 stop，禁止再复述「步骤4完成/主页采集完毕」。"
            f"本回合必须立刻调发文工具（{_posts_todo_hint(task_id)}）。"
            "调完发文后再写步骤8/9/10与终稿。"
            f"（Stop门闩·发文第{n}次）"
        )
    status = _collision_status_line(task_id)
    return (
        "【系统·禁止结束会话】三、账号采集已收口，四、关联碰撞仍在由系统推进中"
        f"（{status}）。禁止 stop，禁止只复述「步骤4完成」。"
        "请保持会话：无工具可调时输出一句「碰撞进行中」即可；"
        "碰撞门禁一开必须立刻对 validated 调用发文工具，禁止结束会话。"
        f"（Stop门闩·碰撞第{n}次）"
    )


def try_advance_collision_before_nudge(store: Any, task_id: str) -> None:
    """门闩触发时顺带推一档碰撞，缩短真空（不涉及续跑）。"""
    try:
        from report_04.engine import advance_collision_phase

        advance_collision_phase(store, task_id, max_rounds=4)
    except Exception as exc:
        logger.warning("stop_gate 推进碰撞失败 task=%s: %s", task_id, exc)


def pre_verify_continue_message(
    store: Any,
    task_id: str,
    *,
    attempt: int = 0,
) -> Optional[str]:
    """供 sink pre_verify：需拦截则返回文案，否则 None。"""
    reason = classify_stop_block(task_id)
    if not reason:
        return None

    if reason == _REASON_COLLISION:
        try_advance_collision_before_nudge(store, task_id)
        reason = classify_stop_block(task_id)
        if not reason:
            logger.info("stop_gate 推进后已可放行 stop task=%s", task_id)
            return None

    msg = build_stop_gate_message(task_id, attempt=attempt, reason=reason)
    logger.warning(
        "stop_gate 拦截主进程 stop task=%s reason=%s attempt=%s",
        task_id,
        reason,
        attempt,
    )
    return msg
