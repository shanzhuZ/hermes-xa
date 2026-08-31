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
  - 补救：仅对 status=running 且 started_at 超过 stale 阈值的节点强制 completed；
    壳已 completed 但仍有未终态假节点 → 仍可 kickoff/heal（防 daemon 线程丢失后永久卡死）
    禁止对建任务预插的 pending 用 updated_at 判超时（会误秒完成）
"""

from __future__ import annotations

import logging
import random
import threading
import time
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
# 超过「最大延时 + 缓冲」仍未终态 → 视为定时线程丢失，强制收口
_STALE_BUFFER_SEC = 30
_STALE_AGE_SEC = _DEMO_DELAY_MAX_SEC + _STALE_BUFFER_SEC  # 105
# 孤儿重挂时的短延时（已 running 且线程丢失但未到 stale）
_REARM_DELAY_MIN_SEC = 8
_REARM_DELAY_MAX_SEC = 20

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


def reconcile_collision_demo_steps(store: Any, task_id: str) -> int:
    """[COLLISION_DEMO_FAKE] 超时强制收口 + 必要时重挂定时器。

    每轮 LLM / 点亮关联碰撞壳时调用。返回本轮强制 completed + 新开定时器数量。
    """
    if not task_id or store is None:
        return 0
    n = heal_stale_collision_demo_steps(store, task_id)
    n += kickoff_collision_demo_steps(store, task_id)
    return n


def heal_stale_collision_demo_steps(store: Any, task_id: str) -> int:
    """[COLLISION_DEMO_FAKE] 仅对已真正启动的 running 假节点做超时强制 completed。

    注意：4.4/4.5/4.6 在建任务时即预插为 pending，updated_at≈created_at。
    禁止用 updated_at 算年龄，否则一进关联碰撞就会被误判「超时补救」。
    """
    if not task_id or store is None:
        return 0
    healed = 0
    for step_key, (short_name, _) in _DEMO_META.items():
        info = _demo_step_row(task_id, step_key)
        if not info:
            continue
        cur = str(info.get("status") or "").strip()
        if cur in {"completed", "skipped", "failed"}:
            with _lock:
                _started.discard((task_id, step_key))
            continue
        # pending 从未 kickoff：不算超时，留给 kickoff 正常拉起
        if cur != "running":
            continue
        age = _running_age_seconds(info)
        # 内存认为有定时器，但已超过最大延时仍未终态 → 当作孤儿，清 token
        with _lock:
            token = (task_id, step_key)
            if token in _started and age is not None and age >= _DEMO_DELAY_MAX_SEC + 5:
                _started.discard(token)
                logger.warning(
                    "[COLLISION_DEMO_FAKE] 清除孤儿定时标记 task=%s step=%s age=%ss",
                    task_id,
                    step_key,
                    int(age),
                )
        if age is None or age < _STALE_AGE_SEC:
            continue
        if _force_complete_demo(store, task_id, step_key, short_name, age):
            healed += 1
    return healed


def kickoff_collision_demo_steps(store: Any, task_id: str) -> int:
    """[COLLISION_DEMO_FAKE] 拉起未终态假节点的延时 completed 定时器。

    - 壳 running：正常演示 kickoff
    - 壳已 completed/skipped 但仍有未终态假节点：补救 kickoff（短延时或正常延时）
    返回新启动的定时器数量。幂等。
    """
    if not task_id or store is None:
        return 0
    from report_04.gates import get_step_status

    shell_st = get_step_status(task_id, PHASE_COLLISION)
    open_keys = []
    for sk in _DEMO_META:
        st = get_step_status(task_id, sk)
        if st in {"completed", "skipped", "failed"}:
            continue
        # pending / running 算未终态；无行则跳过
        if st is None and not _demo_step_row(task_id, sk):
            continue
        open_keys.append(sk)

    if shell_st == "running":
        pass
    elif shell_st in {"completed", "skipped"} and open_keys:
        logger.info(
            "[COLLISION_DEMO_FAKE] 壳已 %s 仍有未终态假节点，进入补救 kickoff task=%s open=%s",
            shell_st,
            task_id,
            open_keys,
        )
    else:
        return 0

    started = 0
    for step_key, (short_name, run_msg) in _DEMO_META.items():
        cur = get_step_status(task_id, step_key)
        if cur in {"completed", "skipped", "failed"}:
            continue
        if cur is None and not _demo_step_row(task_id, step_key):
            continue

        info = _demo_step_row(task_id, step_key) or {}
        # 仅 running 且已有 started_at 才可能 stale；pending 必须走正常延时
        age = _running_age_seconds(info) if cur == "running" else None
        # 已到 stale：交给 heal，不再挂新线程
        if age is not None and age >= _STALE_AGE_SEC:
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

        # 已 running 的补救重挂用短延时；首次 kickoff 用正常随机延时
        if cur == "running" and shell_st in {"completed", "skipped"}:
            delay = random.randint(_REARM_DELAY_MIN_SEC, _REARM_DELAY_MAX_SEC)
        elif cur == "running" and age is not None and age >= _DEMO_DELAY_MIN_SEC:
            # 同进程内疑似丢线程但未 stale：短延时补完
            delay = random.randint(_REARM_DELAY_MIN_SEC, _REARM_DELAY_MAX_SEC)
        else:
            delay = random.randint(_DEMO_DELAY_MIN_SEC, _DEMO_DELAY_MAX_SEC)
        done_msg = f"{short_name}完成"
        try:
            t = threading.Thread(
                target=_complete_after_delay,
                args=(store, task_id, step_key, delay, done_msg),
                name=f"collision-demo-{step_key[:20]}",
                daemon=True,
            )
            t.start()
        except Exception as exc:
            logger.warning(
                "[COLLISION_DEMO_FAKE] 启动定时线程失败 task=%s step=%s: %s",
                task_id,
                step_key,
                exc,
            )
            with _lock:
                _started.discard(token)
            continue
        started += 1
        logger.info(
            "[COLLISION_DEMO_FAKE] 已启动假节点 task=%s step=%s delay=%ss shell=%s",
            task_id,
            step_key,
            delay,
            shell_st,
        )
    return started


def _force_complete_demo(
    store: Any,
    task_id: str,
    step_key: str,
    short_name: str,
    age: float,
) -> bool:
    """超时强制 completed，并清内存标记。"""
    done_msg = f"{short_name}完成（超时补救 {int(age)}s）"
    try:
        store.set_step_status(
            task_id,
            step_key,
            "completed",
            message=done_msg,
            skip_phase_rollup=False,
        )
        logger.warning(
            "[COLLISION_DEMO_FAKE] 超时强制完成 task=%s step=%s age=%ss",
            task_id,
            step_key,
            int(age),
        )
        return True
    except Exception as exc:
        logger.warning(
            "[COLLISION_DEMO_FAKE] 超时强制完成失败 task=%s step=%s: %s",
            task_id,
            step_key,
            exc,
        )
        return False
    finally:
        with _lock:
            _started.discard((task_id, step_key))


def _demo_step_row(task_id: str, step_key: str) -> Optional[Dict[str, Any]]:
    try:
        from collect_01 import db

        return db.fetch_one(
            "SELECT status, started_at, updated_at FROM collect_phase_steps "
            "WHERE task_id=%s AND step_key=%s",
            (task_id, step_key),
        )
    except Exception as exc:
        logger.warning(
            "[COLLISION_DEMO_FAKE] 读步骤行失败 task=%s step=%s: %s",
            task_id,
            step_key,
            exc,
        )
        return None


def _running_age_seconds(row: Dict[str, Any]) -> Optional[float]:
    """只认 started_at 作为「真正开始跑假流程」的时刻。

    禁止用 updated_at/created_at：建任务预插 pending 时这两字段已有值，
    会把「等了几分钟才进关联碰撞」误判成超时。
    """
    raw = row.get("started_at")
    if raw is None:
        return None
    try:
        if hasattr(raw, "timestamp"):
            return max(0.0, time.time() - float(raw.timestamp()))
        from datetime import datetime

        text = str(raw).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(text[:26], fmt)
                return max(0.0, time.time() - dt.timestamp())
            except ValueError:
                continue
    except Exception:
        return None
    return None


def _complete_after_delay(
    store: Any,
    task_id: str,
    step_key: str,
    delay_sec: int,
    done_msg: str,
) -> None:
    """[COLLISION_DEMO_FAKE] 延时后标 completed。"""
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
