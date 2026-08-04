# -*- coding: utf-8 -*-
"""[COLLISION_DEMO_FAKE] 04 写报 · 关联碰撞假流程节点（演示用）。

正式版删除指引（检索关键字 COLLISION_DEMO_FAKE）：
  1. 删除本文件 scripts/report_04/collision_demo_steps.py
  2. phases.py 中假节点 StepDef / COLLISION_DEMO_* / is_collision_demo_step
  3. task_store.py 中 kickoff 触发与壳收口排除
  4. orchestrator.py / sink.py 中对假节点的工具拦截
  5. SourceTag.java / source_tag.py / ReportTaskCreateService 预插三步
  6. docs 若有提及一并删

行为：
  - 「4. 关联碰撞」phase_collision → running 时，4.4/4.5/4.6 变 running
  - 各自独立随机 45～75 秒后 completed
  - 不挡发文门禁；壳收口不等这三个节点；禁止 Agent 为它们调工具
"""

from __future__ import annotations

import logging
import random
import threading
from typing import Any, Dict, Optional, Set, Tuple

from report_04.phases import (
    PHASE_COLLISION,
    STEP6_GEO_VERIFY,
    STEP6_RELATION_GRAPH,
    STEP6_RUMOR_SX,
    COLLISION_DEMO_STEP_KEYS,
    is_collision_demo_step,
)

logger = logging.getLogger(__name__)

# [COLLISION_DEMO_FAKE] 延时区间（秒）
_DEMO_DELAY_MIN_SEC = 45
_DEMO_DELAY_MAX_SEC = 75

# step_key → (标题短名用于完成文案, 启动文案)
_DEMO_META: Dict[str, Tuple[str, str]] = {
    STEP6_GEO_VERIFY: ("地理位置核验", "系统地理位置核验中…"),
    STEP6_RELATION_GRAPH: ("关系网络分析", "系统关系网络分析中…"),
    STEP6_RUMOR_SX: ("陕西谣言特色库", "系统陕西谣言特色库核验中…"),
}

# 已启动定时器的 (task_id, step_key)，防止重复开线程
_started: Set[Tuple[str, str]] = set()
_lock = threading.Lock()


def collision_demo_step_keys():
    """[COLLISION_DEMO_FAKE] 供外部 import 的 frozenset。"""
    return COLLISION_DEMO_STEP_KEYS


def kickoff_collision_demo_steps(store: Any, task_id: str) -> int:
    """[COLLISION_DEMO_FAKE] phase_collision 已 running 时拉起三个假节点。

    返回新启动的定时器数量。幂等。
    """
    if not task_id or store is None:
        return 0
    from report_04.gates import get_step_status

    shell_st = get_step_status(task_id, PHASE_COLLISION)
    if shell_st != "running":
        return 0

    started = 0
    for step_key, (short_name, run_msg) in _DEMO_META.items():
        cur = get_step_status(task_id, step_key)
        if cur in {"completed", "skipped", "failed"}:
            continue
        with _lock:
            token = (task_id, step_key)
            if token in _started:
                continue
            _started.add(token)

        if cur != "running":
            try:
                store.set_step_status(
                    task_id,
                    step_key,
                    "running",
                    message=run_msg,
                    # 假节点点亮父壳时勿再递归 kickoff；壳应已是 running
                    skip_phase_rollup=True,
                )
            except Exception as exc:
                logger.warning(
                    "[COLLISION_DEMO_FAKE] 置 running 失败 task=%s step=%s: %s",
                    task_id,
                    step_key,
                    exc,
                )
                with _lock:
                    _started.discard(token)
                continue

        delay = random.randint(_DEMO_DELAY_MIN_SEC, _DEMO_DELAY_MAX_SEC)
        done_msg = f"{short_name}完成"
        t = threading.Thread(
            target=_complete_after_delay,
            args=(store, task_id, step_key, delay, done_msg),
            name=f"collision-demo-{step_key[:20]}",
            daemon=True,
        )
        t.start()
        started += 1
        logger.info(
            "[COLLISION_DEMO_FAKE] 已启动假节点 task=%s step=%s delay=%ss",
            task_id,
            step_key,
            delay,
        )
    return started


def _complete_after_delay(
    store: Any,
    task_id: str,
    step_key: str,
    delay_sec: int,
    done_msg: str,
) -> None:
    """[COLLISION_DEMO_FAKE] 延时后标 completed。"""
    import time

    try:
        time.sleep(max(1, int(delay_sec)))
        from report_04.gates import get_step_status

        cur = get_step_status(task_id, step_key)
        if cur in {"completed", "skipped", "failed"}:
            return
        store.set_step_status(
            task_id,
            step_key,
            "completed",
            message=done_msg,
            skip_phase_rollup=False,  # 允许尝试收口壳（壳逻辑会忽略假节点）
        )
        logger.info(
            "[COLLISION_DEMO_FAKE] 假节点完成 task=%s step=%s msg=%s",
            task_id,
            step_key,
            done_msg,
        )
    except Exception as exc:
        logger.warning(
            "[COLLISION_DEMO_FAKE] 完成失败 task=%s step=%s: %s",
            task_id,
            step_key,
            exc,
        )
    finally:
        with _lock:
            _started.discard((task_id, step_key))


def block_agent_tool_for_demo_step(
    phase: Optional[str], gate: Optional[str]
) -> Optional[str]:
    """[COLLISION_DEMO_FAKE] Agent 不得以假节点为 phase/gate 调工具。

    返回拦截文案；不拦截则 None。
    """
    ph = str(phase or "").strip()
    gt = str(gate or "").strip()
    if is_collision_demo_step(ph) or is_collision_demo_step(gt):
        return (
            f"[COLLISION_DEMO_FAKE] 节点 {ph or gt} 为系统演示假流程，"
            "禁止 Agent 调用工具；由系统脚本自动完成。"
        )
    return None
