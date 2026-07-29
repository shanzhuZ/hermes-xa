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
from typing import Any, Dict, List, Optional
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
    """发文阶段实质已齐（可进研判），不要求父壳一定已 completed。"""
    try:
        from report_04.gates import posts_substantively_ready

        return bool(posts_substantively_ready(task_id))
    except Exception:
        pass
    try:
        from report_04.gates import can_advance_to_analysis

        if can_advance_to_analysis(task_id).get("ok"):
            return True
    except Exception:
        pass
    if get_step_status(task_id, "step7_posts") in {"completed", "skipped"}:
        return True
    if post_count(task_id) <= 0:
        return False
    try:
        from report_04.step_reconcile import list_unattempted_post_platforms

        return not (list_unattempted_post_platforms(task_id) or [])
    except Exception:
        return False


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
    if get_step_status(task_id, "step11_report") == "completed":
        return True
    try:
        from report_04.report_parser import is_final_report

        row = db.fetch_one(
            """
            SELECT content FROM hermes_user_dialogues
            WHERE task_id=%s AND role='assistant'
            ORDER BY id DESC LIMIT 3
            """,
            (task_id,),
        )
        # 多扫几条
        rows = db.fetch_all(
            """
            SELECT content FROM hermes_user_dialogues
            WHERE task_id=%s AND role='assistant'
            ORDER BY id DESC LIMIT 5
            """,
            (task_id,),
        )
        for r in rows or []:
            if is_final_report(str((r or {}).get("content") or "")):
                return True
        _ = row
    except Exception:
        pass
    return False


def post_count(task_id: str) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s", (task_id,)
    )
    return int((row or {}).get("c") or 0)


def infer_continue_kind(task_id: str) -> Optional[str]:
    """hold | posts | analysis | report；已完成则 None。"""
    task = db.fetch_one(
        "SELECT status FROM hermes_tasks WHERE task_id=%s", (task_id,)
    ) or {}
    if str(task.get("status") or "") not in {"running", "pending"}:
        return None
    if has_final_report(task_id):
        return None

    from report_04.gates import can_run_step7_collect

    step7 = get_step_status(task_id, "step7_posts")
    if can_run_step7_collect(task_id):
        if step7 not in {"completed", "skipped"} or post_count(task_id) <= 0:
            try:
                from report_04.step_reconcile import list_unattempted_post_platforms

                todo = list_unattempted_post_platforms(task_id) or []
                if todo or post_count(task_id) <= 0:
                    return "posts"
            except Exception:
                return "posts"
        return "analysis"

    s5 = get_step_status(task_id, "step5_streams")
    s4 = get_step_status(task_id, "step4_profiles")
    s6 = get_step_status(task_id, "step6_validated")
    s43 = get_step_status(task_id, "step6_osint_es")
    if s43 in {"pending", "running"} or s6 in {"pending", "running"}:
        return "hold"
    if s5 in {"completed", "skipped", "running"} or s4 in {"completed", "skipped"}:
        return "hold"
    return None


def build_continue_message(kind: str, task_id: str) -> str:
    if kind == "posts":
        msg = (
            "【系统续跑·禁止结束会话】发文已开放。请立即对每个尚未尝试的 validated 平台"
            "调用对应发文工具（Twitter→mcp_twitter_get_user_tweets；"
            "YouTube→mcp_youtube_analyze_channel_videos；微博→mcp_weibo_get_feeds；"
            "其余 Apify Actor→run→dataset）。"
            "禁止写「等待系统/会话保持」。发文调用后输出步骤8/9/10，再写以「一、账号基本信息」开头的终稿。"
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
    if kind == "analysis":
        return (
            "【系统续跑·禁止结束会话】发文已完成。请立即并行输出步骤8/9/10分析正文，"
            "再输出以「一、账号基本信息」开头的步骤11终稿。禁止等待句、禁止 done。"
        )
    return (
        "【系统续跑·禁止结束会话】系统正在推进 4.1.2/4.2/4.3。"
        "禁止写「等待系统」并结束。无工具可调则保持会话；"
        "门禁放行发文后本回合必须立刻调发文工具，再写研判与终稿。"
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
    """未出终稿且仍可续跑 / 正在续跑 → finalize 暂缓 failed。"""
    if has_final_report(task_id):
        return False
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


def _posts_or_analysis_busy(task_id: str) -> bool:
    """发文/视频/研判仍 running 时禁止叠开续跑。"""
    if get_step_status(task_id, "step7_posts") == "running":
        return True
    rows = db.fetch_all(
        """
        SELECT step_key FROM collect_phase_steps
        WHERE task_id=%s
          AND status='running'
          AND (
            step_key LIKE 'step7_post_%%'
            OR step_key LIKE 'step7_video_%%'
            OR step_key IN (
              'step8_img_analysis','step9_context_views',
              'step10_context_pii','step11_report'
            )
          )
        LIMIT 1
        """,
        (task_id,),
    )
    return bool(rows)


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
    if continue_inflight(task_id):
        return "已有续跑在飞"
    if _posts_or_analysis_busy(task_id):
        return "发文/研判仍 running"
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
        ok = gateway_chat_stream_once(session_id, task_id, message)
    except Exception as exc:
        logger.warning("continue_worker 异常 task=%s: %s", task_id, exc)
        ok = False
    finally:
        clear_continue_inflight(task_id, store)

    # inflight 已清后再决定重试 / 上限检查（避免假在飞挡重试）
    try:
        if has_final_report(task_id):
            # SSE 期间主会话可能已出终稿：必须补标，禁止只 return 留下 running
            _ensure_completed_if_final_report(store, task_id)
            return
        if not ok and next_retry < _CONTINUE_MAX:
            time.sleep(6.0)
            maybe_continue_agent_session(
                store, task_id, reason=f"retry_fail:{reason}"
            )
        elif ok and next_retry >= _CONTINUE_MAX:
            maybe_continue_agent_session(
                store, task_id, reason="check_after_last", force=False
            )
    except Exception as exc:
        logger.warning("continue_worker 收尾异常 task=%s: %s", task_id, exc)


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
        if _INFLIGHT.get(task_id):
            return False
        payload = _payload(task_id)
        retries = int(payload.get("flow_continue_retries") or 0)
        if not force and retries >= _CONTINUE_MAX:
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
        next_retry = retries + 1

    try:
        payload = _payload(task_id)
        payload["flow_continue_retries"] = next_retry
        payload["flow_continue_inflight"] = 1
        payload["flow_continue_kind"] = use_kind
        payload["flow_continue_reason"] = str(reason or "")[:120]
        payload["flow_continue_started_at"] = time.time()
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
    kind = infer_continue_kind(task_id) or "hold"
    base = build_continue_message(kind, task_id)
    try:
        from report_04.engine import build_agent_context

        eng = build_agent_context(task_id)
        if eng:
            return base + "\n\n" + eng
    except Exception:
        pass
    return base
