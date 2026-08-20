# -*- coding: utf-8 -*-
"""04 写报：未出终稿时同 session 强制续跑（取消「等系统→stream 断→砍步骤」死锁）。

主路径仍由 Agent 调工具；续跑 N 次后仍无发文则系统兜底采发文（能采则采），
再催写研判/终稿；仍无终稿则明确 failed。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib import request as urlrequest

from collect_01 import db
from report_04.gates import get_step_status

logger = logging.getLogger(__name__)

_CONTINUE_MAX = int(os.environ.get("HERMES_REPORT_CONTINUE_MAX", "2") or "2")
# 续跑 SSE 墙钟：须盖住发文+研判+终稿；可用环境变量覆盖
_CONTINUE_HTTP_TIMEOUT = int(
    os.environ.get("HERMES_REPORT_CONTINUE_TIMEOUT", "900") or "900"
)
# inflight 超过墙钟+缓冲视为假在飞，允许自愈
_CONTINUE_STALE_SEC = int(
    os.environ.get(
        "HERMES_REPORT_CONTINUE_STALE_SEC",
        str(_CONTINUE_HTTP_TIMEOUT + 120),
    )
    or str(_CONTINUE_HTTP_TIMEOUT + 120)
)
_LOCK = threading.Lock()
_INFLIGHT: Dict[str, bool] = {}
# 被「在飞/忙」跳过的续跑可延期；清 inflight 或视频终态后再补催
_DEFER_SKIP_REASONS = (
    "已有续跑在飞",
    "当前动作续跑在飞",
    "发文/研判仍 running",
    "osint_done 时续跑冷却中",
)

AgentAction = Literal["call", "hold", "write", "done"]

# Windows 脱离 Hook 进程标志
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_CREATE_NO_WINDOW = 0x08000000

_WAIT_MARKERS = (
    "等待系统完成",
    "等待系统收口",
    "等待系统推进",
    "等待系统",
    "等系统",
    "待系统完成",
    "待系统收口",
    "等待 4.2",
    "等待4.2",
    "等待步骤6",
    "等待步骤 6",
    "会话保持中",
    "后进入步骤4.3",
    "后进入步骤7",
    "后再进入步骤7",
    "后再采发文",
    "系统正在收敛",
    "系统正在/即将收敛",
)


def looks_like_wait_exit(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return any(m in t for m in _WAIT_MARKERS)


_RETRO_MARKERS = (
    "[文本核验结论]",
    "[社工库核验结论]",
    "步骤5：",
    "步骤5:",
    "步骤6：",
    "步骤6:",
    "步骤4.3：",
    "步骤4.3:",
    "步骤7：",
    "步骤7:",
)


def _posts_ready_for_analysis(task_id: str) -> bool:
    """可进研判：只认 step7_posts 父壳终态。"""
    try:
        from report_04.gates import can_advance_to_analysis

        return bool(can_advance_to_analysis(task_id).get("ok"))
    except Exception:
        return get_step_status(task_id, "step7_posts") in {"completed", "skipped"}


def looks_like_retrospective_without_analysis(task_id: str, text: str) -> bool:
    """识别「倒写 5/6/4.3/7 回顾后收束、却未写研判/终稿」的假完成。

    对应：发文已齐 → 模型抢跑失败 → 回写旧结论 → stream 结束 → 六/七父壳不跑。
    """
    t = (text or "").strip()
    if not t or len(t) < 80:
        return False
    if has_final_report(task_id):
        return False
    try:
        from report_04.report_parser import is_final_report, looks_like_report_attempt

        if is_final_report(t) or looks_like_report_attempt(t):
            return False
    except Exception:
        if "一、账号基本信息" in t.replace(" ", "").replace("\u3000", ""):
            return False
    if not _posts_ready_for_analysis(task_id):
        return False
    try:
        from report_04.gates import analysis_steps_terminal

        if analysis_steps_terminal(task_id):
            return False
    except Exception:
        pass
    # 分析步仍未实质推进
    for sk in ("step8_img_analysis", "step9_context_views", "step10_context_pii"):
        if get_step_status(task_id, sk) in {"completed", "skipped"}:
            return False
    hits = sum(1 for m in _RETRO_MARKERS if m in t)
    if hits < 2:
        return False
    # 排除已在写步骤8～10 正文（不仅是 thinking 里提一句）
    analysis_body_hints = (
        "步骤8：",
        "步骤8:",
        "步骤9：",
        "步骤9:",
        "步骤10：",
        "步骤10:",
        "[图片流分析]",
        "[上下文观点]",
        "[隐私信息]",
    )
    # 若同时有回顾标记 + 明确 8/9/10 章节标题，交给正常路径，不强制续跑
    if sum(1 for m in analysis_body_hints if m in t) >= 2:
        return False
    return True


def _gateway_base_and_key() -> tuple[str, str]:
    base = (
        os.environ.get("HERMES_GATEWAY_URL")
        or os.environ.get("API_SERVER_URL")
        or "http://127.0.0.1:8642"
    ).rstrip("/")
    key = (
        os.environ.get("API_SERVER_KEY")
        or os.environ.get("HERMES_API_SERVER_KEY")
        or "dc9b5db558aa4844d0a29d79deb296b75aba58a670c79ecff3ed922839b2c86b"
    )
    return base, key


def gateway_chat_stream_once(session_id: str, task_id: str, message: str) -> bool:
    """同 session 再开一轮 chat/stream（带墙钟 deadline，避免子进程挂死占 inflight）。"""
    base, key = _gateway_base_and_key()
    url = f"{base}/api/sessions/{session_id}/chat/stream"
    body = json.dumps({"input": message}, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "text/event-stream",
            "X-Hermes-Session-Key": f"task:{task_id}",
        },
    )
    deadline = time.time() + float(_CONTINUE_HTTP_TIMEOUT)
    try:
        with urlrequest.urlopen(req, timeout=_CONTINUE_HTTP_TIMEOUT) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            if int(code or 0) >= 400:
                logger.warning("flow continue HTTP %s task=%s", code, task_id)
                return False
            while True:
                if time.time() >= deadline:
                    logger.warning(
                        "flow continue SSE 墙钟超时 task=%s timeout=%ss",
                        task_id,
                        _CONTINUE_HTTP_TIMEOUT,
                    )
                    return False
                line = resp.readline()
                if not line:
                    break
        logger.info("flow continue SSE 结束 task=%s", task_id)
        return True
    except Exception as exc:
        logger.warning("flow continue Gateway 失败 task=%s: %s", task_id, exc)
        return False


def spawn_detached_python_module(module: str, cli_args: List[str]) -> bool:
    """脱离当前 Hook 进程拉起 python -m <module> …（Windows/Unix）。"""
    scripts_dir = Path(__file__).resolve().parent.parent
    repo_root = scripts_dir.parent
    cmd = [sys.executable, "-m", module, *cli_args]
    env = os.environ.copy()
    prev = (env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = (
        str(scripts_dir) if not prev else f"{scripts_dir}{os.pathsep}{prev}"
    )
    if not (env.get("HERMES_HOME") or "").strip():
        env["HERMES_HOME"] = str(repo_root)
    kwargs: Dict[str, Any] = {
        "cwd": str(scripts_dir),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP（CREATE_NO_WINDOW 减少闪窗）
        kwargs["creationflags"] = (
            _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP | _CREATE_NO_WINDOW
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)
        logger.info("spawn_detached module=%s args=%s", module, cli_args)
        return True
    except Exception as exc:
        logger.warning("spawn_detached 失败 module=%s: %s", module, exc)
        return False


def spawn_detached_continue(
    task_id: str,
    reason: str,
    kind: str,
    *,
    after_max: bool = False,
) -> bool:
    """拉起独立续跑子进程（活过 Hook 120s 杀进程）。"""
    args = ["--task-id", str(task_id), "--reason", str(reason or ""), "--kind", str(kind or "")]
    if after_max:
        args.append("--after-max")
    return spawn_detached_python_module("report_04.continue_worker", args)


def clear_continue_inflight(task_id: str, store: Any = None) -> None:
    """清内存 + DB flow_continue_inflight=0。"""
    with _LOCK:
        _INFLIGHT.pop(task_id, None)
    try:
        if store is None:
            from report_04.task_store import TaskStore

            store = TaskStore()
        p = _payload(task_id)
        if int(p.get("flow_continue_inflight") or 0) != 0:
            p["flow_continue_inflight"] = 0
            _write_payload(store, task_id, p)
    except Exception as exc:
        logger.warning("clear_continue_inflight 失败 task=%s: %s", task_id, exc)


def _mark_deferred_continue(
    store: Any,
    task_id: str,
    *,
    kind: str,
    reason: str,
    skip: str,
) -> None:
    """续跑被跳过时记下延期目标，避免门禁已开却永不再催。"""
    try:
        if store is None:
            from report_04.task_store import TaskStore

            store = TaskStore()
        p = _payload(task_id)
        p["flow_continue_deferred"] = 1
        p["flow_continue_deferred_kind"] = str(kind or "")[:32]
        p["flow_continue_deferred_reason"] = str(reason or "")[:120]
        p["flow_continue_deferred_skip"] = str(skip or "")[:120]
        p["flow_continue_deferred_at"] = time.time()
        _write_payload(store, task_id, p)
        logger.info(
            "flow continue 已延期 task=%s kind=%s skip=%s reason=%s",
            task_id,
            kind,
            skip,
            str(reason or "")[:60],
        )
    except Exception as exc:
        logger.warning("标记延期续跑失败 task=%s: %s", task_id, exc)


def _clear_deferred_continue(store: Any, task_id: str) -> None:
    try:
        p = _payload(task_id)
        if not int(p.get("flow_continue_deferred") or 0):
            return
        p["flow_continue_deferred"] = 0
        p.pop("flow_continue_deferred_kind", None)
        p.pop("flow_continue_deferred_reason", None)
        p.pop("flow_continue_deferred_skip", None)
        p.pop("flow_continue_deferred_at", None)
        _write_payload(store, task_id, p)
    except Exception as exc:
        logger.warning("清除延期续跑失败 task=%s: %s", task_id, exc)


def flush_deferred_continue(
    store: Any,
    task_id: str,
    *,
    trigger: str = "flush",
) -> bool:
    """若有延期续跑且门禁仍需催，则补发一次。"""
    if has_final_report(task_id):
        _clear_deferred_continue(store, task_id)
        return False
    p = _payload(task_id)
    if not int(p.get("flow_continue_deferred") or 0):
        return False
    deferred_kind = str(p.get("flow_continue_deferred_kind") or "").strip()
    next_action = next_agent_action(task_id)
    if next_action == "done":
        _clear_deferred_continue(store, task_id)
        return False
    use_kind = deferred_kind or _action_to_continue_kind(next_action, task_id)
    if not use_kind:
        _clear_deferred_continue(store, task_id)
        return False
    # 先清标记，避免 spawn 路径再次 skip 时重复堆叠；若仍 skip 会再 mark
    _clear_deferred_continue(store, task_id)
    return maybe_continue_agent_session(
        store,
        task_id,
        reason=f"deferred:{trigger}",
        kind=use_kind,
        force=False,
    )


def chain_continue_after_worker(
    store: Any,
    task_id: str,
    *,
    prev_kind: str,
    prev_reason: str = "",
) -> bool:
    """本轮续跑结束后按 next_agent_action 链式催下一阶段。"""
    if has_final_report(task_id):
        return False
    next_action = next_agent_action(task_id)
    if next_action == "done":
        return False
    next_kind = _action_to_continue_kind(next_action, task_id)
    if not next_kind:
        return False
    prev = str(prev_kind or "").strip()
    prev_action = _action_for_continue_kind(prev)
    # call→write：posts SSE 结束后必须能接到研判续跑
    if prev_action == "call" and next_action == "write":
        return maybe_continue_agent_session(
            store,
            task_id,
            reason=f"chain_call_to_write:{prev_reason}"[:120],
            kind=next_kind,
            force=False,
        )
    # hold 结束后必可接到 posts/write；同 kind 不重复链式（交给 retry/deferred）
    if prev == next_kind and prev != "hold":
        if not (prev == "analysis" and _analysis_still_open(task_id)):
            return False
    if next_kind not in {"posts", "analysis", "hold"}:
        return False
    return maybe_continue_agent_session(
        store,
        task_id,
        reason=f"chain_after_{prev or 'continue'}:{prev_reason}"[:120],
        kind=next_kind,
        force=False,
    )


def heal_stale_continue_inflight(task_id: str, store: Any = None) -> bool:
    """若 inflight 过期则自清；返回 True 表示已自愈（现视为未在飞）。"""
    payload = _payload(task_id)
    if not int(payload.get("flow_continue_inflight") or 0):
        return False
    ts = payload.get("flow_continue_started_at")
    try:
        started = float(ts) if ts is not None else 0.0
    except Exception:
        started = 0.0
    if not started:
        return False
    age = time.time() - started
    if age <= float(_CONTINUE_STALE_SEC):
        return False
    logger.warning(
        "flow continue stale inflight 自愈 task=%s age=%.0fs stale_sec=%s",
        task_id,
        age,
        _CONTINUE_STALE_SEC,
    )
    clear_continue_inflight(task_id, store)
    return True


def _payload(task_id: str) -> Dict[str, Any]:
    row = db.fetch_one(
        "SELECT payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, "step11_report"),
    )
    raw = (row or {}).get("payload_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return dict(raw) if isinstance(raw, dict) else {}


def _write_payload(store: Any, task_id: str, payload: Dict[str, Any]) -> None:
    store.ensure_step_row(task_id, "step11_report")
    cur = get_step_status(task_id, "step11_report") or "pending"
    if cur in {"completed", "failed", "skipped"}:
        store.set_step_status(
            task_id, "step11_report", cur, payload=payload, force_reopen=True
        )
    else:
        store.set_step_status(
            task_id,
            "step11_report",
            "pending" if cur not in {"pending", "running"} else cur,
            message="等待终稿（系统续跑中）",
            payload=payload,
            force_reopen=True,
        )


def has_final_report(task_id: str) -> bool:
    """仅认正式终稿：step11 completed 或 dialogues.summary。草稿 assistant_reply 不算。"""
    if get_step_status(task_id, "step11_report") == "completed":
        return True
    row = db.fetch_one(
        "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
        (task_id,),
    )
    return bool(row)


def post_count(task_id: str) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s", (task_id,)
    )
    return int((row or {}).get("c") or 0)


def next_agent_action(task_id: str) -> AgentAction:
    """下一步 Agent 该做什么：call 调工具 / hold 等系统管线 / write 研判终稿 / done。

    续跑 busy、链式 handoff、post_llm 提示均以此为准；不看「未来父壳 running」。
    """
    if has_final_report(task_id):
        return "done"
    task = db.fetch_one(
        "SELECT status FROM hermes_tasks WHERE task_id=%s", (task_id,)
    ) or {}
    if str(task.get("status") or "") not in {"running", "pending"}:
        return "done"

    # 研判 / 终稿
    try:
        from report_04.gates import can_advance_to_analysis

        if can_advance_to_analysis(task_id).get("ok"):
            return "write"
    except Exception:
        if get_step_status(task_id, "step7_posts") in {"completed", "skipped"}:
            return "write"

    from report_04.gates import can_run_step7_collect, discovery_steps_terminal

    # 步骤七：发文工具 vs 等视频/收口
    if can_run_step7_collect(task_id):
        s7 = get_step_status(task_id, "step7_posts")
        if s7 not in {"completed", "skipped"}:
            try:
                from report_04.step_reconcile import list_unattempted_post_platforms

                todo = list_unattempted_post_platforms(task_id) or []
                if todo or post_count(task_id) <= 0:
                    return "call"
            except Exception:
                if post_count(task_id) <= 0:
                    return "call"
            # 帖已采齐、父壳未终态：常见为等视频后台
            return "hold"
        return "write"

    # 4.1 / 4.2 / 4.3 系统管线
    s43 = get_step_status(task_id, "step6_osint_es")
    s6 = get_step_status(task_id, "step6_validated")
    if s43 in {"pending", "running"} or s6 in {"pending", "running"}:
        return "hold"

    s5 = get_step_status(task_id, "step5_streams")
    if s5 in {"pending", "running"}:
        return "hold"

    # 发现 / 主页：须 Agent 调工具（Maigret / 网页 / Apify 主页）
    if not discovery_steps_terminal(task_id):
        return "call"
    s4 = get_step_status(task_id, "step4_profiles")
    if s4 in {"pending", "running"}:
        return "call"

    return "hold"


def _action_for_continue_kind(kind: str) -> AgentAction:
    k = str(kind or "").strip().lower()
    if k == "analysis":
        return "write"
    if k == "posts":
        return "call"
    if k == "hold":
        return "hold"
    return "hold"


def _action_to_continue_kind(action: AgentAction, task_id: str) -> Optional[str]:
    """续跑 worker 仍用 hold/posts/analysis 三 kind；由 action 映射。"""
    if action == "done":
        return None
    if action == "write":
        return "analysis"
    if action == "hold":
        return "hold"
    # call
    if _can_run_step7(task_id):
        return "posts"
    return "hold"


def _can_run_step7(task_id: str) -> bool:
    try:
        from report_04.gates import can_run_step7_collect

        return bool(can_run_step7_collect(task_id))
    except Exception:
        return False


def _inflight_continue_action(task_id: str) -> Optional[AgentAction]:
    """当前续跑 SSE 对应的动作；无在飞返回 None。"""
    if not continue_inflight(task_id):
        return None
    kind = str(_payload(task_id).get("flow_continue_kind") or "hold")
    return _action_for_continue_kind(kind)


def _continue_action_busy(task_id: str, *, target_kind: str) -> bool:
    """仅当「同动作」续跑仍在飞时视为 busy。

    例：posts(call) 在飞不挡 write；系统点亮的 step8 running 不挡 write。
    """
    inflight = _inflight_continue_action(task_id)
    if inflight is None:
        return False
    target = _action_for_continue_kind(str(target_kind or ""))
    return inflight == target


def build_next_action_hint(task_id: str) -> str:
    """post_llm / 工具后回注：一句话说明下一步动作。"""
    action = next_agent_action(task_id)
    if action == "done":
        return ""
    if action == "write":
        return (
            "【下一步·write】发文与视频已齐。请立即并行输出步骤8/9/10分析正文，"
            "再写以「一、账号基本信息」开头的步骤11终稿。禁止结束会话、禁止 done。"
        )
    if action == "call":
        if _can_run_step7(task_id):
            try:
                from report_04.step_reconcile import list_unattempted_post_platforms

                todo = list_unattempted_post_platforms(task_id) or []
                if todo:
                    hints = "; ".join(
                        f"{x.get('platform')}→{x.get('tool_hint')}" for x in todo[:6]
                    )
                    return (
                        "【下一步·call】立刻调用发文工具，禁止结束会话。"
                        f" 尚未尝试：{hints}"
                    )
            except Exception:
                pass
            return "【下一步·call】立刻调用各平台发文工具，禁止结束会话。"
        return (
            "【下一步·call】立刻继续采集（Maigret / 网页检索 / 主页 Apify 等），"
            "禁止写「等待系统」并结束会话。"
        )
    return (
        "【下一步·hold】系统管线进行中（图片核验 / 认定 / 社工库 / 视频）。"
        "保持会话，禁止 done；管线收口后系统将催下一步。"
    )


def infer_continue_kind(task_id: str) -> Optional[str]:
    """hold | posts | analysis；已完成则 None。内部转调 next_agent_action。"""
    return _action_to_continue_kind(next_agent_action(task_id), task_id)


def build_continue_message(kind: str, task_id: str) -> str:
    action = _action_for_continue_kind(kind)
    if action == "call" and kind == "hold":
        # 步骤七未开放时的 call（发现/主页）
        hint = build_next_action_hint(task_id)
        msg = (
            "【系统续跑·禁止结束会话】下一步须调采集工具（Maigret / 网页检索 / 主页 Apify 等）。"
            "禁止写「等待系统/会话保持」并结束。"
        )
        if hint:
            msg += "\n" + hint
        return msg
    if kind == "posts" or (action == "call" and kind == "posts"):
        msg = (
            "【系统续跑·禁止结束会话】发文已开放。请立即对每个尚未尝试的 validated 平台"
            "调用对应发文工具（Twitter→mcp_twitter_get_user_tweets；"
            "YouTube→mcp_youtube_analyze_channel_videos；微博→mcp_weibo_get_feeds；"
            "其余 Apify Actor→run→dataset）。"
            "禁止写「等待系统/会话保持」。"
            "须等 step7_posts 父壳终态（含视频）后才能写步骤8/9/10与「一、账号基本信息」终稿。"
        )
        try:
            from report_04.step_reconcile import list_unattempted_post_platforms

            todo = list_unattempted_post_platforms(task_id) or []
            if todo:
                plats = ",".join(str(x.get("platform") or "") for x in todo[:12])
                hints = "; ".join(
                    f"{x.get('platform')}:{x.get('tool_hint')}" for x in todo[:6]
                )
                msg += f" 尚未尝试：{plats}。工具提示：{hints}"
        except Exception:
            pass
        return msg
    if kind == "hold":
        if get_step_status(task_id, "step7_posts") in {"pending", "running"}:
            return (
                "【系统续跑·禁止结束会话】内容采集（step7_posts）尚未终态。"
                "须等发文父壳 completed/skipped（含视频终态）后再写步骤8/9/10与终稿。"
                "禁止提前输出研判正文；禁止结束会话；禁止同步 mcp_video2frame_*。"
            )
        return (
            "【系统续跑·禁止结束会话】系统正在推进 4.1.2/4.2/4.3 或内容采集收口。"
            "禁止写「等待系统」并结束。无工具可调则保持会话；"
            "门禁放行后再写研判与终稿。"
        )
    if kind == "analysis":
        wait_hint = ""
        try:
            from report_04.video_report import format_report_wait_hint

            wait_hint = format_report_wait_hint(task_id)
        except Exception:
            wait_hint = ""
        if wait_hint:
            return (
                "【系统续跑·禁止结束会话】发文已齐。"
                + wait_hint
                + "禁止等待句、禁止 done。"
            )
        return (
            "【系统续跑·禁止结束会话】发文与视频已完成。请立即并行输出步骤8/9/10分析正文，"
            "再输出以「一、账号基本信息」开头的步骤11终稿。禁止等待句、禁止 done。"
        )
    return (
        "【系统续跑·禁止结束会话】系统正在推进流程。"
        "禁止写「等待系统」并结束。无工具可调则保持会话。"
    )


def continue_retries(task_id: str) -> int:
    return int(_payload(task_id).get("flow_continue_retries") or 0)


def continue_inflight(task_id: str) -> bool:
    """是否有续跑在飞；过期 inflight 会自愈后视为未在飞。"""
    with _LOCK:
        if _INFLIGHT.get(task_id):
            return True
    if heal_stale_continue_inflight(task_id):
        return False
    return bool(int(_payload(task_id).get("flow_continue_inflight") or 0))


def should_defer_finalize(task_id: str) -> bool:
    """未出终稿且仍可续跑 / 正在续跑 / 内容采集父壳未终态 → finalize 暂缓 failed。

    发文 MCP 卡住（父壳未齐且无视频可等）不得长期 defer：由 session_end skip 失败子步。
    父壳 running 且发文实质已齐时多为等视频，应 defer。
    """
    if has_final_report(task_id):
        return False
    try:
        from report_04.gates import posts_substantively_ready

        s7 = get_step_status(task_id, "step7_posts")
        # 父壳未终态但发文实质已齐：等视频关父壳，暂缓 finalize
        if s7 in {"pending", "running"} and posts_substantively_ready(task_id):
            return True
    except Exception:
        pass
    if continue_inflight(task_id):
        return True
    kind = infer_continue_kind(task_id)
    if not kind:
        return False
    return continue_retries(task_id) < _CONTINUE_MAX


def fail_task_clearly(store: Any, task_id: str, reason: str) -> None:
    """明确失败收口：跳过未完步骤，任务 failed（可重跑）。"""
    msg = (reason or "续跑耗尽，流程未完成")[:500]
    from report_04.phases import (
        ANALYSIS_STEP_KEYS,
        PHASE_DONE,
        POST_PARENT_STEP_KEY,
    )

    for sk in list(ANALYSIS_STEP_KEYS) + [
        "step11_report",
        POST_PARENT_STEP_KEY,
        "phase_analysis",
        "phase_report",
        "phase_content",
    ]:
        st = get_step_status(task_id, sk)
        if st in {"pending", "running"}:
            store.set_step_status(task_id, sk, "skipped", message=msg[:200])
    # 未尝试发文子步
    try:
        rows = db.fetch_all(
            """
            SELECT step_key, status FROM collect_phase_steps
            WHERE task_id=%s AND step_key LIKE 'step7_post_%%'
              AND status IN ('pending', 'running')
            """,
            (task_id,),
        )
        for r in rows or []:
            store.set_step_status(
                task_id, str(r["step_key"]), "skipped", message=msg[:200]
            )
    except Exception:
        pass
    try:
        db.execute(
            """
            UPDATE hermes_tasks
            SET status='failed',
                current_phase=%s,
                error_message=%s,
                finished_at=COALESCE(finished_at, NOW(3)),
                updated_at=NOW(3)
            WHERE task_id=%s AND status NOT IN ('failed', 'completed', 'cancelled')
            """,
            (PHASE_DONE, msg, task_id),
        )
    except Exception as exc:
        logger.warning("fail_task_clearly 更新任务失败 task=%s: %s", task_id, exc)
    try:
        from report_04.thought_progress import emit_system_thinking

        emit_system_thinking(task_id, f"【系统】任务已失败：{msg[:180]}。请重新发起写报。")
    except Exception:
        pass
    logger.warning("flow continue 耗尽，任务 failed task=%s reason=%s", task_id, msg[:120])


def try_system_posts_fallback(store: Any, task_id: str) -> bool:
    """系统兜底采发文：先回放已有工具输出；再尽力直采 twitter。

    返回 True 表示已有帖或本轮采到帖。
    """
    try:
        from report_04.step_reconcile import reconcile_step7_from_post_tools

        reconcile_step7_from_post_tools(store, task_id)
    except Exception as exc:
        logger.info("系统发文回放跳过 task=%s: %s", task_id, exc)

    if post_count(task_id) > 0:
        try:
            from report_04.step_reconcile import close_collect_parent_if_ready
            from report_04.phases import POST_PARENT_STEP_KEY

            close_collect_parent_if_ready(
                store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕"
            )
        except Exception:
            pass
        return True

    # 直采 twitter（cookies 可用时）
    got = 0
    try:
        got = _fallback_collect_twitter_posts(store, task_id)
    except Exception as exc:
        logger.warning("twitter 系统兜底采发文失败 task=%s: %s", task_id, exc)

    if post_count(task_id) > 0 or got > 0:
        try:
            from report_04.step_reconcile import close_collect_parent_if_ready
            from report_04.phases import POST_PARENT_STEP_KEY

            store.set_step_status(
                task_id,
                POST_PARENT_STEP_KEY,
                "completed",
                message="系统兜底发文完成",
                force_reopen=True,
            )
            close_collect_parent_if_ready(
                store, task_id, POST_PARENT_STEP_KEY, "系统兜底发文完成"
            )
        except Exception:
            pass
        try:
            from report_04.thought_progress import emit_system_thinking

            emit_system_thinking(
                task_id,
                f"【系统·步骤5】兜底发文完成（{post_count(task_id)} 条），请继续写研判与终稿。",
            )
        except Exception:
            pass
        return True
    return False


def _fallback_collect_twitter_posts(store: Any, task_id: str) -> int:
    """用 twikit cookies 直采 twitter 发文（失败返回 0，不抛穿）。"""
    from report_04.phases import post_platform_step_key
    from report_04.step_reconcile import list_unattempted_post_platforms

    todo = list_unattempted_post_platforms(task_id) or []
    need_tw = any(str(x.get("platform") or "") == "twitter" for x in todo)
    # 即使不在 unattempted，有 validated twitter 且无帖也采
    if not need_tw and post_count(task_id) > 0:
        return 0

    rows = db.fetch_all(
        """
        SELECT account_id, account_handle, profile_url FROM collect_validated_accounts
        WHERE task_id=%s AND verdict='validated' AND platform='twitter'
        """,
        (task_id,),
    )
    if not rows:
        seeds = db.fetch_all(
            """
            SELECT account_id, account_handle, profile_url FROM collect_profiles
            WHERE task_id=%s AND platform='twitter' LIMIT 3
            """,
            (task_id,),
        )
        rows = seeds or []
    if not rows:
        return 0

    try:
        import asyncio
        from pathlib import Path

        # 与 MCP 启动器一致：twitter_mcp.server.COOKIES_PATH
        from twitter_mcp._vendor.twikit import Client
        import twitter_mcp.server as tw_srv

        cookies_path = Path(getattr(tw_srv, "COOKIES_PATH", "") or "")
        if not cookies_path.is_file():
            logger.info("系统兜底：无 twitter cookies，跳过直采 task=%s", task_id)
            return 0

        proxy = None
        for key in ("TWITTER_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
            val = (os.environ.get(key) or "").strip()
            if val:
                proxy = val
                break

        async def _fetch(handle: str, count: int = 100):
            cookies = json.loads(cookies_path.read_text(encoding="utf-8"))
            client = Client("en", proxy=proxy)
            client.set_cookies(
                {"auth_token": cookies["auth_token"], "ct0": cookies["ct0"]}
            )
            user = await client.get_user_by_screen_name(handle.lstrip("@"))
            tweets = await client.get_user_tweets(
                user.id, tweet_type="Tweets", count=count
            )
            return user, tweets

        total = 0
        for r in rows[:3]:
            handle = str(r.get("account_handle") or "").strip().lstrip("@")
            if not handle and r.get("profile_url"):
                handle = str(r["profile_url"]).rstrip("/").split("/")[-1]
            if not handle:
                continue
            post_key = post_platform_step_key("twitter")
            store.ensure_step_row(task_id, post_key)
            store.set_step_status(
                task_id,
                post_key,
                "running",
                message="系统兜底采发文中",
                force_reopen=True,
            )
            try:
                user, tweets = asyncio.run(_fetch(handle))
            except Exception as exc:
                logger.warning("twitter 直采失败 @%s: %s", handle, exc)
                store.set_step_status(
                    task_id,
                    post_key,
                    "failed",
                    message=f"系统兜底采发文失败：{str(exc)[:120]}",
                )
                continue
            # twikit 对象 → normalize 可吃的结构
            items = []
            for t in tweets or []:
                item = {
                    "id": getattr(t, "id", None) or getattr(t, "id_str", None),
                    "text": getattr(t, "text", None) or getattr(t, "full_text", None),
                    "created_at": str(getattr(t, "created_at", "") or ""),
                    "user": {
                        "rest_id": str(getattr(user, "id", "") or ""),
                        "screen_name": handle,
                    },
                }
                items.append(item)
            from collect_01.normalizers.registry import dispatch

            ctx = {
                "task_id": task_id,
                "platform_hint": "twitter",
                "account_id": str(
                    r.get("account_id") or getattr(user, "id", "") or handle
                ),
            }
            data = dispatch(
                "mcp_twitter_get_user_tweets",
                {"tweets": items, "user": {"rest_id": str(getattr(user, "id", "")), "screen_name": handle}},
                ctx,
            )
            posts = data.get("posts") or []
            if posts:
                store.save_post_rows(posts, step_key=post_key)
                store.set_step_status(
                    task_id,
                    post_key,
                    "completed",
                    message=f"系统兜底已入库发文 {len(posts)} 条",
                    force_reopen=True,
                )
                total += len(posts)
            else:
                store.set_step_status(
                    task_id,
                    post_key,
                    "skipped",
                    message="系统兜底：0 条发文",
                )
        return total
    except ImportError as exc:
        logger.info("系统兜底：twitter_mcp 不可用 task=%s: %s", task_id, exc)
        return 0


def _max_assistant_id(task_id: str) -> int:
    row = db.fetch_one(
        """
        SELECT MAX(id) AS i FROM hermes_user_dialogues
        WHERE task_id=%s AND role='assistant'
        """,
        (task_id,),
    )
    return int((row or {}).get("i") or 0)


def _seconds_since_last_agent_activity(task_id: str) -> Optional[float]:
    """距最近助手对话或工具输出的秒数；无记录返回 None。"""
    ages: List[float] = []
    row = db.fetch_one(
        """
        SELECT TIMESTAMPDIFF(SECOND, MAX(created_at), NOW(3)) AS age
        FROM hermes_user_dialogues
        WHERE task_id=%s AND role='assistant'
        """,
        (task_id,),
    )
    if row and row.get("age") is not None:
        ages.append(float(row["age"]))
    row = db.fetch_one(
        """
        SELECT TIMESTAMPDIFF(SECOND, MAX(executed_at), NOW(3)) AS age
        FROM hermes_tool_outputs
        WHERE task_id=%s
        """,
        (task_id,),
    )
    if row and row.get("age") is not None:
        ages.append(float(row["age"]))
    if not ages:
        return None
    return min(ages)


def _analysis_still_open(task_id: str) -> bool:
    from report_04.gates import analysis_steps_terminal

    return not analysis_steps_terminal(task_id)


def _recent_tool_activity(task_id: str, *, within_sec: float = 45.0) -> bool:
    """近期有工具成功/失败输出 → Agent 仍在干活，勿因 session_end 叠催。"""
    try:
        row = db.fetch_one(
            """
            SELECT TIMESTAMPDIFF(SECOND, MAX(created_at), NOW(3)) AS age_sec
            FROM hermes_tool_outputs
            WHERE task_id=%s
            """,
            (task_id,),
        )
        age = (row or {}).get("age_sec")
        if age is None:
            return False
        return float(age) <= float(within_sec)
    except Exception:
        return False


def _continue_cooldown_active(task_id: str, *, within_sec: float = 25.0) -> bool:
    """上一轮续跑刚发起不久 → 禁止立刻再续（尤其 session_end 回声）。"""
    payload = _payload(task_id)
    ts = payload.get("flow_continue_started_at")
    if not ts:
        return False
    try:
        # 存的是 unix 秒
        started = float(ts)
        return (time.time() - started) < float(within_sec)
    except Exception:
        return False


def _should_skip_continue(
    task_id: str,
    *,
    reason: str,
    kind: str,
    force: bool,
) -> Optional[str]:
    """返回跳过原因；None 表示可以续跑。"""
    if force:
        return None
    if _continue_action_busy(task_id, target_kind=str(kind or "")):
        return "当前动作续跑在飞"
    # session_end 是「上一轮 stream 关了」的回声，最容易叠催
    if str(reason or "").startswith("session_end"):
        if _continue_cooldown_active(task_id, within_sec=25.0):
            return "续跑冷却中（避免 session_end 回声）"
        if _recent_tool_activity(task_id, within_sec=45.0):
            return "近期仍有工具活动"
        # 发文已齐且在等写报：允许 session_end 催 analysis；否则若仍缺帖由 osint/其它路径催
        if kind == "posts" and post_count(task_id) > 0:
            return "已有发文，不必再催 posts"
    # osint_done 与 session_end 几乎同时：若已记过续跑且还在冷却，跳过
    if str(reason or "") == "osint_done" and _continue_cooldown_active(
        task_id, within_sec=15.0
    ):
        return "osint_done 时续跑冷却中"
    return None


def run_after_continue_max(store: Any, task_id: str) -> None:
    """续跑达上限后：系统发文兜底，再催研判或明确 failed。"""
    try:
        ok_posts = try_system_posts_fallback(store, task_id)
        if ok_posts and not has_final_report(task_id):
            p2 = _payload(task_id)
            p2["flow_continue_retries"] = 0
            p2["flow_posts_fallback"] = 1
            _write_payload(store, task_id, p2)
            maybe_continue_agent_session(
                store,
                task_id,
                reason="after_posts_fallback",
                kind="analysis",
                force=True,
            )
        elif not has_final_report(task_id):
            fail_task_clearly(
                store,
                task_id,
                "续跑已达上限且未产出终稿（发文系统兜底未成功或 Agent 未写报）。请重跑任务。",
            )
    except Exception as exc:
        logger.warning("continue 上限后处理失败 task=%s: %s", task_id, exc)
        fail_task_clearly(
            store, task_id, f"续跑上限后收口失败：{str(exc)[:120]}"
        )


def _ensure_completed_if_final_report(store: Any, task_id: str) -> None:
    """已有终稿时把 hermes_tasks 标 completed（续跑跳过/收尾兜底）。"""
    try:
        from report_04.engine import _try_finalize_report

        _try_finalize_report(store, task_id, light_only=True)
    except Exception as exc:
        logger.warning("终稿后补标 completed 失败 task=%s: %s", task_id, exc)


def run_continue_worker_job(
    store: Any,
    task_id: str,
    *,
    reason: str = "",
    kind: str = "",
) -> None:
    """continue_worker 主逻辑：读 session、打 Gateway，finally 必清 inflight。"""
    payload = _payload(task_id)
    next_retry = int(payload.get("flow_continue_retries") or 0)
    use_kind = str(kind or payload.get("flow_continue_kind") or "").strip() or (
        infer_continue_kind(task_id) or "hold"
    )
    task = db.fetch_one(
        "SELECT status, session_id FROM hermes_tasks WHERE task_id=%s",
        (task_id,),
    ) or {}
    session_id = str(task.get("session_id") or "").strip()
    ok = False
    try:
        if not session_id:
            logger.warning("continue_worker 无 session_id task=%s", task_id)
            return
        if has_final_report(task_id):
            logger.info("continue_worker 已有终稿，补标 completed 后跳过 task=%s", task_id)
            _ensure_completed_if_final_report(store, task_id)
            return
        if use_kind == "analysis":
            try:
                from report_04.orchestrator import advance_to_analysis_phase

                advance_to_analysis_phase(
                    store, task_id, f"续跑点亮研判:{reason}"[:80]
                )
            except Exception as exc:
                logger.warning("continue 点亮研判失败 task=%s: %s", task_id, exc)
        message = build_continue_message(use_kind, task_id)
        time.sleep(1.5 if next_retry <= 1 else 0.8)
        logger.info(
            "continue_worker 开始 SSE task=%s kind=%s attempt=%s/%s reason=%s",
            task_id,
            use_kind,
            next_retry,
            _CONTINUE_MAX,
            str(reason or "")[:60],
        )
        before_aid = _max_assistant_id(task_id)
        ok = gateway_chat_stream_once(session_id, task_id, message)
        # 空 SSE / 会话已死：HTTP 200 但无新助手输出，不能当成功（否则同 kind 不再催）
        if ok and use_kind in {"analysis", "posts"}:
            if _max_assistant_id(task_id) <= before_aid:
                logger.warning(
                    "continue SSE 无新助手输出，视为失败 task=%s kind=%s",
                    task_id,
                    use_kind,
                )
                ok = False
    except Exception as exc:
        logger.warning("continue_worker 异常 task=%s: %s", task_id, exc)
        ok = False
    finally:
        clear_continue_inflight(task_id, store)
        # 提前 return（无 session / 已有终稿）时也要清延期，避免永久挂起
        try:
            if has_final_report(task_id) or not session_id:
                _clear_deferred_continue(store, task_id)
        except Exception:
            pass

    # inflight 已清后再决定：失败重试 / 链式续跑 / 延期补催 / 达上限检查
    try:
        if has_final_report(task_id):
            # SSE 期间主会话可能已出终稿：必须补标，禁止只 return 留下 running
            _ensure_completed_if_final_report(store, task_id)
            _clear_deferred_continue(store, task_id)
            return
        if not session_id:
            return
        if not ok and next_retry < _CONTINUE_MAX:
            time.sleep(6.0)
            maybe_continue_agent_session(
                store, task_id, reason=f"retry_fail:{reason}"
            )
        elif ok:
            chained = chain_continue_after_worker(
                store, task_id, prev_kind=use_kind, prev_reason=str(reason or "")
            )
            # posts(call) 结束且 next=write：链式未发起时再 handoff 一次
            if not chained and use_kind == "posts":
                if next_agent_action(task_id) == "write":
                    maybe_continue_agent_session(
                        store,
                        task_id,
                        reason="after_posts_handoff",
                        kind="analysis",
                        force=False,
                    )
            elif not chained and next_retry >= _CONTINUE_MAX:
                maybe_continue_agent_session(
                    store, task_id, reason="check_after_last", force=False
                )
        # 无论成败，消化「在飞/忙」期间积压的延期续跑
        flush_deferred_continue(store, task_id, trigger=f"after_{use_kind}")
    except Exception as exc:
        logger.warning("continue_worker 收尾异常 task=%s: %s", task_id, exc)


def maybe_nudge_stalled_analysis(
    store: Any,
    task_id: str,
    *,
    min_quiet_seconds: float = 90.0,
) -> bool:
    """发文已齐、研判未终态、Agent 静默过久 → 点亮步骤8～10 并再催续跑。"""
    if has_final_report(task_id):
        return False
    try:
        from report_04.gates import can_advance_to_analysis

        if not can_advance_to_analysis(task_id).get("ok"):
            return False
    except Exception:
        return False
    if not _analysis_still_open(task_id):
        return False
    if next_agent_action(task_id) != "write":
        return False
    age = _seconds_since_last_agent_activity(task_id)
    if min_quiet_seconds > 0 and (age is None or age < float(min_quiet_seconds)):
        return False
    try:
        from report_04.orchestrator import advance_to_analysis_phase

        advance_to_analysis_phase(store, task_id, "研判静默过久，系统点亮")
    except Exception as exc:
        logger.warning("nudge 点亮研判失败 task=%s: %s", task_id, exc)
    return maybe_continue_agent_session(
        store,
        task_id,
        reason="analysis_stalled_quiet",
        kind="analysis",
        force=False,
    )


def maybe_continue_agent_session(
    store: Any,
    task_id: str,
    *,
    reason: str = "",
    kind: Optional[str] = None,
    force: bool = False,
) -> bool:
    """未出终稿时同 session 续跑（只 spawn 子进程，不阻塞读 SSE）。返回是否已发起。"""
    if has_final_report(task_id):
        return False
    use_kind = kind or infer_continue_kind(task_id)
    if not use_kind:
        return False
    target_action = next_agent_action(task_id)
    if target_action == "done":
        return False
    # 请求的 kind 与当前 next 不一致时，以 next 为准（避免 deferred 旧 kind 误催）
    expected_kind = _action_to_continue_kind(target_action, task_id)
    if expected_kind and not kind:
        use_kind = expected_kind
    elif kind and expected_kind and _action_for_continue_kind(kind) != target_action:
        use_kind = expected_kind
    # 8～10 已齐但发文/视频未终态：不催写报，等 video_runner 终态后再续
    try:
        from report_04.gates import analysis_steps_terminal
        from report_04.video_report import can_write_report_after_videos

        if analysis_steps_terminal(task_id) and not can_write_report_after_videos(task_id).get("ok"):
            logger.info(
                "flow continue 等待视频/发文终态 task=%s reason=%s",
                task_id,
                reason,
            )
            return False
    except Exception:
        pass

    skip = _should_skip_continue(
        task_id, reason=str(reason or ""), kind=use_kind, force=force
    )
    if skip:
        logger.info(
            "flow continue 跳过 task=%s reason=%s kind=%s skip=%s",
            task_id,
            reason,
            use_kind,
            skip,
        )
        # 在飞/忙导致跳过：记下延期，避免门禁已开却永不再催
        if any(str(skip).startswith(x) or str(skip) == x for x in _DEFER_SKIP_REASONS):
            _mark_deferred_continue(
                store, task_id, kind=use_kind, reason=str(reason or ""), skip=str(skip)
            )
        return False

    task = db.fetch_one(
        "SELECT status, session_id FROM hermes_tasks WHERE task_id=%s",
        (task_id,),
    ) or {}
    if str(task.get("status") or "") not in {"running", "pending"}:
        return False
    session_id = str(task.get("session_id") or "").strip()
    if not session_id:
        return False

    with _LOCK:
        if _INFLIGHT.get(task_id) and _continue_action_busy(task_id, target_kind=use_kind):
            _mark_deferred_continue(
                store,
                task_id,
                kind=use_kind,
                reason=str(reason or ""),
                skip="当前动作续跑在飞",
            )
            return False
        payload = _payload(task_id)
        retries = int(payload.get("flow_continue_retries") or 0)
        # hold 不占用 posts/analysis 配额，避免 hold 占满后无法催发文
        count_toward_max = use_kind != "hold"
        if not force and count_toward_max and retries >= _CONTINUE_MAX:
            logger.info(
                "flow continue 达上限 task=%s retries=%s → spawn 系统发文兜底",
                task_id,
                retries,
            )
            # 达上限：脱离 Hook 做发文兜底 / failed
            spawned = spawn_detached_continue(
                task_id, reason=str(reason or "max"), kind=use_kind, after_max=True
            )
            return bool(spawned)
        _INFLIGHT[task_id] = True
        next_retry = (retries + 1) if count_toward_max else retries

    try:
        payload = _payload(task_id)
        payload["flow_continue_retries"] = next_retry
        payload["flow_continue_inflight"] = 1
        payload["flow_continue_kind"] = use_kind
        payload["flow_continue_action"] = _action_for_continue_kind(use_kind)
        payload["flow_continue_reason"] = str(reason or "")[:120]
        payload["flow_continue_started_at"] = time.time()
        # 发起成功则清延期标记（本轮会实际催）
        payload["flow_continue_deferred"] = 0
        payload.pop("flow_continue_deferred_kind", None)
        payload.pop("flow_continue_deferred_reason", None)
        payload.pop("flow_continue_deferred_skip", None)
        payload.pop("flow_continue_deferred_at", None)
        _write_payload(store, task_id, payload)
    except Exception:
        pass

    # 保持任务 running
    try:
        db.execute(
            """
            UPDATE hermes_tasks
            SET status='running', finished_at=NULL, error_message=NULL, updated_at=NOW(3)
            WHERE task_id=%s AND status NOT IN ('failed', 'completed', 'cancelled')
            """,
            (task_id,),
        )
    except Exception:
        pass

    logger.info(
        "flow continue 发起(detached) task=%s kind=%s attempt=%s/%s reason=%s",
        task_id,
        use_kind,
        next_retry,
        _CONTINUE_MAX,
        reason[:60],
    )

    ok = spawn_detached_continue(task_id, str(reason or ""), use_kind)
    # Hook 进程内不再持有 inflight；真相源在 DB，由 worker finally 清
    with _LOCK:
        _INFLIGHT.pop(task_id, None)
    if not ok:
        # spawn 失败立刻自清，避免永久卡住
        clear_continue_inflight(task_id, store)
        return False
    return True


def anti_wait_followup_context(task_id: str) -> str:
    """post_llm 检测到「等系统」时注入的硬约束。"""
    hint = build_next_action_hint(task_id)
    kind = infer_continue_kind(task_id) or "hold"
    base = build_continue_message(kind, task_id)
    if hint and hint not in base:
        base = base + "\n" + hint
    try:
        from report_04.engine import build_agent_context

        eng = build_agent_context(task_id)
        if eng:
            return base + "\n\n" + eng
    except Exception:
        pass
    return base
