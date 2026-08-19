"""Hermes Hook 入口 — 04 账号画像写报入库（stdin JSON）。

冲突约定（方案 C，见 docs/思考流与步骤树同步落库实施方案.md）：
- Java 中继可粗写 pending/running→running；Vision/OCR/Apify 禁止粗 completed；
- Hook 为细状态真相源。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any, Dict, List, Optional

from collect_01.config import hermes_home
from collect_01.db import DbError
from collect_01.normalizers.base import infer_mcp_server
from collect_01.normalizers.registry import dispatch
from collect_01.normalizers.apify import apify_platform_from_actor_tool, resolve_apify_platform_hint, actor_reported_empty_dataset, apify_fail_message
from collect_01.seed_platforms import (
    APIFY_SEED_PLATFORM_TOOLS,
    REPORT_SEED_PROFILE_TOOLS as SEED_PROFILE_TOOLS,
    TOOL_TO_SEED_PLATFORM,
    apify_seed_empty_ok,
    is_apify_seed_platform,
)
from report_04.account_parser import looks_like_seed_done, parse_seed_from_assistant
from report_04.candidate_parser import (
    looks_like_step3_summary,
    parse_web_search_candidates,
    parse_web_search_candidates_from_tool,
)
from report_04.gates import (
    analysis_steps_terminal,
    can_advance_to_analysis,
    can_advance_to_osint,
    can_advance_to_step5,
    can_advance_to_step7,
    can_complete_step11,
    can_run_step7_collect,
    can_run_step3_web_search,
    can_update_step4_children,
    can_update_step7_children,
    discovery_steps_terminal,
    get_step_status,
    is_stream_compare_ready,
    seconds_since_last_tool,
    step4_profile_collect_started,
    step7_collect_active,
)
from report_04.phases import (
    ANALYSIS_STEP_KEYS,
    APIFY_POST_TOOLS,
    APIFY_TOOL_PLATFORM,
    OSINT_ES_STEP_KEY,
    POST_TOOLS,
    PROFILE_TOOLS,
    STEP4_COLLECT_TOOLS,
    TOOL_PLATFORM,
    TOOL_PRIMARY_STEP,
    WEB_SEARCH_TOOLS,
    post_platform_step_key,
    profile_platform_step_key,
    tool_collect_step_key,
    tool_step_key,
)
from report_04.report_parser import (
    backfill_analysis_from_report,
    detect_skip_steps,
    is_final_report,
    is_progress_only,
    mentions_analysis_steps,
    parse_standalone_analysis_blocks,
)
from report_04.task_store import TaskStore, is_report_intent

_LOG_DIR = hermes_home() / "logs"
_LOG_FILE = _LOG_DIR / "report_04_sink.log"
logger = logging.getLogger(__name__)

_SKIP_STEP_TOOLS = frozenset({
    "skill_view", "clarify", "tool_search", "describe_tool", "todo", "terminal",
    "mcp_firecrawl_firecrawl_search", "mcp_firecrawl_firecrawl_scrape",
})
# 步骤3 网页检索：缩短安静期 + 墙钟/次数上限（甲方：耗时长且信息少）
_STEP3_QUIET_SECONDS = 15
_STEP3_MAX_WALL_SECONDS = 180  # 最多约 3 分钟
_STEP3_MAX_WEB_SEARCH = 3  # web_search 成功次数上限
_STEP3_MAX_WEB_TOOLS = 5  # 全部 web/browser 工具成功次数上限
# 达标门槛：extract≥1 或 browser≥1 或 search≥2（原 search≥3）
_STEP3_MIN_SEARCH_FOR_DONE = 2
# 步骤5 vision 安静期：图片流已齐后仍等模型可能继续发的 vision
_STEP5_VISION_SETTLE_SECONDS = 5  # 批次闭环：图片齐后短安静期即收口，不再等 25s
_VISION_TOOL_NAMES = ("vision_analyze", "mcp_vision_analyze", "mcp_ocr_perform_ocr", "mcp_ocr_perform_batch_ocr")
_STEP5_STREAM_TOOLS = frozenset({"mcp_ocr_perform_ocr", "mcp_vision_analyze", "vision_analyze"})
_SEEN_TOOL_CALLS: set = set()
_LAST_APIFY_HINT: Dict[str, str] = {}


def _setup_logging() -> None:
    if logger.handlers:
        return
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [report_04] %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError:
        pass


_setup_logging()


def handle_event(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = payload.get("hook_event_name") or ""
    if event == "pre_llm_call":
        return _on_pre_llm(payload)
    if event == "pre_tool_call":
        return _on_pre_tool(payload)
    if event == "post_tool_call":
        _on_post_tool(payload)
    elif event == "post_llm_call":
        return _on_post_llm_call(payload)
    elif event == "pre_verify":
        return _on_pre_verify(payload)
    elif event == "on_session_end":
        _on_session_end(payload)
    return None


def _on_pre_verify(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """主进程即将 stop：碰撞真空时返回 continue，拦截结束（不改续跑）。"""
    ex = _extra(payload)
    task_id = _resolve_task_id(payload)
    if not task_id:
        return None
    try:
        attempt = int(ex.get("attempt") or 0)
    except Exception:
        attempt = 0
    try:
        from report_04.stop_gate import pre_verify_continue_message

        msg = pre_verify_continue_message(_store(), task_id, attempt=attempt)
    except Exception as exc:
        logger.warning("pre_verify stop_gate 失败 task=%s: %s", task_id, exc)
        return None
    if not msg:
        return None
    return {"action": "continue", "message": msg}


def _normalize_hook_tool_name(raw: Optional[str]) -> str:
    from collect_01.normalizers.base import normalize_mcp_tool_name

    return normalize_mcp_tool_name(str(raw or "").strip())


def _is_skipped_step_tool(
    tool_name: str,
    task_id: str,
    *,
    tool_args: Optional[Dict[str, Any]] = None,
    phase: Optional[str] = None,
) -> Optional[str]:
    """已 skipped 的节点禁止模型再调对应工具（不论当初为何 skip）。"""
    keys: List[str] = []
    if phase:
        keys.append(str(phase).strip())
    plat = TOOL_PLATFORM.get(tool_name) or APIFY_TOOL_PLATFORM.get(tool_name)
    if not plat:
        plat = _infer_platform(tool_name, tool_args or {}, [], "")
    plat = str(plat or "").strip().lower()
    if plat:
        keys.append(profile_platform_step_key(plat))
        keys.append(post_platform_step_key(plat))
    primary = TOOL_PRIMARY_STEP.get(tool_name)
    if primary:
        keys.append(str(primary))

    post_capable = (
        tool_name in POST_TOOLS
        or tool_name in APIFY_POST_TOOLS
        or tool_name in {"mcp_apify_get_actor_run", "mcp_apify_get_dataset_items"}
    )
    seen = set()
    for sk in keys:
        if not sk or sk in seen:
            continue
        seen.add(sk)
        if get_step_status(task_id, sk) != "skipped":
            continue
        # 双用途 Apify：主页已 skip，但发文子步未 skip 且步骤7已开放 → 允许当发文工具
        if sk.startswith("step4_profile_") and plat and post_capable:
            post_st = get_step_status(task_id, post_platform_step_key(plat))
            if can_run_step7_collect(task_id) and post_st not in {"skipped", "completed", "failed"}:
                continue
        if sk == "step4_profiles" and post_capable and can_run_step7_collect(task_id):
            continue
        return (
            f"{sk} 已 skipped，禁止再次调用 {tool_name}。"
            "已跳过的节点不可补采；请继续其它未终态步骤。"
        )
    return None


def _is_premature_osint_tool(tool_name: str, task_id: str) -> Optional[str]:
    """4.1+4.2 未完成时禁止社工库工具，防止抢跑点亮 4.3。"""
    try:
        from report_04.osint_es import is_osint_es_tool
    except Exception:
        return None
    if not is_osint_es_tool(tool_name):
        return None
    if can_advance_to_osint(task_id).get("ok"):
        return None
    s4 = get_step_status(task_id, "step4_profiles")
    s5 = get_step_status(task_id, "step5_streams")
    s6 = get_step_status(task_id, "step6_validated")
    return (
        f"步骤4.3 社工库尚未开放（step4={s4 or 'pending'} step5={s5 or 'pending'} "
        f"step6={s6 or 'pending'}）。禁止调用 {tool_name}。"
        "须先完成步骤3全部主页子节点，再等 4.1/4.2 终态后由系统或本步调用社工库。"
    )


def _is_premature_step7_tool(tool_name: str, task_id: str) -> Optional[str]:
    """步骤五/六未完成时禁止发文类工具。返回拦截原因，允许则 None。"""
    if can_run_step7_collect(task_id):
        return None
    s4 = get_step_status(task_id, "step4_profiles")
    s5 = get_step_status(task_id, "step5_streams")
    s6 = get_step_status(task_id, "step6_validated")
    # MCP 发文工具：一律拦截（文案必须按真实 gate 指引，禁止误导为空等）
    if tool_name in POST_TOOLS and tool_name != "mcp_apify_get_dataset_items":
        if s4 not in {"completed", "skipped"}:
            pend = None
            try:
                from report_04.gates import _step4_profile_children_pending

                pend = _step4_profile_children_pending(task_id)
            except Exception:
                pend = "step4_profile_*"
            return (
                f"步骤7发文尚未开放：当前仍在步骤4（未完成 {pend or '主页子步'}）。"
                "禁止 get_user_tweets / 发文类工具。"
                "请立刻继续完成或跳过剩余主页采集；禁止结束会话。"
                "步骤4全部终态后系统会自动跑 4.1/4.2/4.3，"
                "同一会话下一轮编排变为 step7_posts 后必须立即调发文工具。"
            )
        s43 = get_step_status(task_id, "step6_osint_es")
        return (
            f"步骤7发文尚未开放（step5={s5 or 'pending'} step6={s6 or 'pending'} "
            f"step6_osint_es={s43 or 'pending'}）。"
            "禁止发文类工具。禁止结束会话空等；"
            "保持同一会话，待系统收口 4.1/4.2/4.3 后下一轮立刻补采各平台发文。"
            "禁止输出「等待系统完成后再发文」后 done。"
        )
    # 步骤四已收口后的 Apify：只可能是抢跑步骤7
    apify_like = (
        tool_name in APIFY_POST_TOOLS
        or tool_name in {"mcp_apify_get_actor_run", "mcp_apify_get_dataset_items"}
    )
    if apify_like and s4 in {"completed", "skipped"}:
        s43 = get_step_status(task_id, "step6_osint_es")
        return (
            f"步骤4已完成，但步骤7尚未开放（step5={s5 or 'pending'} step6={s6 or 'pending'} "
            f"step6_osint_es={s43 or 'pending'}）。"
            "禁止提前用 Apify 采发文；禁止结束会话空等；"
            "待 4.2+4.3 终态后同一会话立即采发文。"
        )
    return None


def _is_late_web_search_tool(tool_name: str, task_id: str) -> Optional[str]:
    """步骤四主页采集已开始后禁止继续 web_search（避免 stream 还在步骤3、树已进步骤4）。

    例外：步骤四仍有 pending/running 的 youtube 子节点时，允许仅用于解析 UC channelId 的检索。
    """
    if tool_name not in WEB_SEARCH_TOOLS:
        return None
    if not step4_profile_collect_started(task_id):
        return None
    # YouTube 仅有 @handle 时：允许在步骤4期间用 web 解析 UC（否则工具硬要 UC… 必然跳过）
    yt_st = get_step_status(task_id, "step4_profile_youtube")
    if yt_st in {"pending", "running"} and tool_name in {"web_search", "web_extract"}:
        return None
    return (
        "步骤4主页采集已开始，禁止再调用 web_search/web_extract/browser_*。"
        "请继续候选主页 MCP/Apify 采集，勿回到步骤3检索。"
        "（例外：仅当 youtube 子步骤仍 pending/running 时可用 web_search/web_extract 解析 UC channelId。）"
    )


def _pending_image_stream_lines(task_id: str) -> List[str]:
    from collect_01 import db as _db

    rows = _db.fetch_all(
        """
        SELECT source_platform, payload_url FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image' AND validation_status='pending'
        ORDER BY source_platform, stream_id
        """,
        (task_id,),
    )
    lines: List[str] = []
    for row in rows:
        plat = str(row.get("source_platform") or "?")
        url = str(row.get("payload_url") or "").strip()
        if url:
            lines.append(f"  · {plat}: {url[:220]}")
    return lines


def _is_redundant_step5_vision(tool_name: str, tool_args: Dict[str, Any], task_id: str) -> Optional[str]:
    """该 URL 对应图片流已终态时禁止重复 vision。"""
    if tool_name not in {"mcp_vision_analyze", "vision_analyze"}:
        return None
    from collect_01.image_stream_match import find_image_stream_for_tool

    stream = find_image_stream_for_tool(task_id, tool_args)
    if not stream:
        return None
    st = str(stream.get("validation_status") or "")
    if st in {"processed", "pass", "fail"}:
        pending = _pending_image_stream_lines(task_id)
        tail = "\n".join(pending[:6]) if pending else "（无 pending，请停止 vision 等待步骤6）"
        return (
            f"该图片流已 vision 终态({st})，禁止重复调用。"
            f"请并行处理剩余 pending（一次齐发 vision_analyze）：\n{tail}"
        )
    return None


def _is_premature_step5_tool(tool_name: str, task_id: str) -> Optional[str]:
    """步骤4未终态禁止 vision；步骤5已收口后禁止再 vision（批次闭环，不回开）。

    拦截文案按真实编排 gate 分支，禁止在步骤7+ 仍写「请进入步骤6」。
    """
    if tool_name not in _STEP5_STREAM_TOOLS:
        return None
    s5 = get_step_status(task_id, "step5_streams")
    if s5 in {"completed", "skipped"}:
        try:
            from report_04.orchestrator import infer_gate_step

            gate = infer_gate_step(task_id)
        except Exception:
            gate = ""
        if gate == "step7_posts":
            return (
                "步骤5图片流已收口，禁止再调用 vision/OCR。"
                "当前为步骤7发文：请继续调发文工具；发文齐后写步骤8/9/10与终稿，勿回补 vision。"
            )
        if gate in {
            "step8_img_analysis",
            "step9_context_views",
            "step10_context_pii",
            "step11_report",
        }:
            wait_hint = ""
            try:
                from report_04.video_report import format_report_wait_hint

                wait_hint = format_report_wait_hint(task_id)
            except Exception:
                wait_hint = ""
            if wait_hint:
                return (
                    "步骤5图片流已收口，禁止再调用 vision/OCR。"
                    + wait_hint
                )
            return (
                "步骤5图片流已收口，禁止再调用 vision/OCR。"
                "当前为研判/写报：请直接输出步骤8/9/10分析正文与「一、账号基本信息」终稿。"
            )
        if gate in {"step6_validated", "step6_osint_es"}:
            return (
                "步骤5图片流已收口，禁止再调用 vision/OCR。"
                "当前为步骤6/4.3：保持会话，终态后立刻调步骤7发文工具，勿回补 vision。"
            )
        return (
            "步骤5图片流已收口，禁止再调用 vision/OCR。"
            "请按当前编排步骤继续，勿回补 vision（系统不再回开步骤5）。"
        )
    from report_04.gates import is_stream_compare_ready

    if s5 == "running" and is_stream_compare_ready(task_id):
        return (
            "步骤5全部图片流已终态，禁止再 vision/OCR。"
            "系统正在收口步骤5并推进步骤6/4.3，请勿重复调用 vision。"
        )
    from report_04.gates import step4_profiles_terminal

    if step4_profiles_terminal(task_id):
        return None
    # Agent 已抢跑步骤5：对其它主页已推进仍从未尝试的子步 fail-forward，
    # 避免「口头 skip / 无候选」只写在对话里导致永久卡死；已尝试平台不误杀。
    try:
        from report_04.step_reconcile import fail_forward_step4_unattempted_when_siblings_done

        n = fail_forward_step4_unattempted_when_siblings_done(
            _store(),
            task_id,
            reason="抢跑步骤5时未尝试主页采集，已跳过",
        )
        if n and step4_profiles_terminal(task_id):
            return None
    except Exception as exc:
        logger.warning("step4 fail-forward(抢跑vision) 失败 task=%s: %s", task_id, exc)
    pending = None
    try:
        from report_04.gates import _step4_profile_children_pending

        pending = _step4_profile_children_pending(task_id)
    except Exception:
        pending = "step4_profile_*"
    return (
        f"步骤4主页子节点未全部终态（仍有 {pending or 'pending/running'}），禁止 vision/OCR。"
        "请先完成或显式 skip 剩余平台（尤其 youtube 需 UC channelId、github 用 Apify username），"
        "禁止因其它平台已采完提前进入步骤5。"
    )


def _is_invalid_youtube_channel_id(tool_name: str, tool_args: Dict[str, Any]) -> Optional[str]:
    """YouTube MCP：允许 UC… 或账号名/@handle/URL（由 MCP 自动解析）；仅拦空值与伪短 UC。"""
    if tool_name not in {
        "mcp_youtube_get_channel_stats",
        "mcp_youtube_analyze_channel_videos",
    }:
        return None
    from collect_01.normalizers.youtube import youtube_channel_id_ok

    cid = str(tool_args.get("channelId") or tool_args.get("channel_id") or "").strip()
    if not cid:
        return "YouTube 缺少 channelId：请传 UC… 或用户账号名/@handle。"
    if youtube_channel_id_ok(cid):
        return None
    # 伪 UC（UC + 过短）：拦截；纯 handle / @xxx / URL 放行给 MCP 解析
    if cid.upper().startswith("UC") and len(cid) < 22:
        return (
            f"YouTube 伪 channelId={cid!r}（UC 过短）。"
            "请传正式 UC… 或账号名/@handle，禁止编造伪 UC。"
        )
    return None


def _terminal_profile_failure_reason(
    tool_name: str,
    tool_output: str = "",
    tool_args: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """主页工具不可恢复失败 → 应 skipped，禁止长期停在 running。

    典型：YouTube Channel not found（正式 UC 已查过仍不存在）。
    """
    text = str(tool_output or "")
    low = text.lower()
    args = tool_args if isinstance(tool_args, dict) else {}
    if tool_name == "mcp_youtube_get_channel_stats":
        cid = str(args.get("channelId") or args.get("channel_id") or "").strip()
        # 已是正式 UC 且明确 not found：再等重试无意义
        if cid.upper().startswith("UC") and len(cid) >= 22:
            if "not found" in low or "channel with id" in low:
                return f"YouTube 频道不存在（{cid}），已跳过"
        if "not found" in low and ("channel" in low or "频道" in text):
            return "YouTube 频道不存在，已跳过"
    if tool_name in PROFILE_TOOLS:
        # 其它主页 MCP 的明确 not found / 404
        if "not found" in low or "404" in low or "does not exist" in low:
            if "channel" in low or "user" in low or "profile" in low or "账号" in text:
                return "主页不存在或已删除，已跳过"
    return None


def _step5_guidance_context(task_id: str) -> Optional[str]:
    """步骤五进行中时注入硬约束，约束模型勿抢跑发文。"""
    s5 = get_step_status(task_id, "step5_streams")
    if s5 not in {"pending", "running"}:
        return None
    if can_run_step7_collect(task_id):
        return None
    from report_04.gates import count_image_streams, count_image_streams_processed

    n_img = count_image_streams(task_id)
    n_done = count_image_streams_processed(task_id)
    pending_lines = _pending_image_stream_lines(task_id)
    if pending_lines:
        n_pend = len(pending_lines)
        return (
            "【写报硬约束·步骤5】"
            f"尚有 {n_pend} 条图片流未 vision 终态（进度 {n_done}/{n_img}）。"
            f"本回合必须并行调用 {n_pend} 次 vision_analyze（一次齐发，禁止逐条多轮拖延）。"
            "image_url 必须严格使用下列 payload_url：\n"
            + "\n".join(pending_lines)
            + "\n禁止：get_user_tweets、analyze_channel_videos、get_user_feeds、任何 Apify 发文。"
        )
    if n_img > 0 and n_done >= n_img:
        return (
            "【写报硬约束·步骤5】全部图片流已终态，禁止再 vision/OCR。"
            "可输出 [文本核验结论]；禁止写「等待系统完成 4.2/4.3」后结束会话。"
            "请保持会话：系统收口并进入步骤6/4.3 后，下一轮必须立刻调发文工具。"
        )
    return (
        "【写报硬约束·当前步骤5】必须完成全部图片流 vision 后才能进入步骤6/7。"
        f"图片流进度 {n_done}/{n_img}。"
        "禁止调用：get_user_tweets、analyze_channel_videos、get_user_feeds、"
        "以及任何 Apify 发文采集。步骤6 validated 完成前禁止发文工具。"
        "禁止结束会话空等。"
    )


def _step7_ready_guidance(task_id: str) -> Optional[str]:
    """4.3 已放行且仍有未尝试发文平台：强制催调，禁止「等系统」收尾。"""
    if not can_run_step7_collect(task_id):
        return None
    try:
        from report_04.step_reconcile import list_unattempted_post_platforms

        todo = list_unattempted_post_platforms(task_id)
    except Exception:
        return None
    if not todo:
        return None
    lines = [
        "【写报硬约束·步骤7发文已开放】禁止结束会话，禁止只写 4.1/等待系统。",
        f"本回合必须对下列 {len(todo)} 个 validated 调用发文工具：",
    ]
    for item in todo[:12]:
        lines.append(f"- {item.get('platform')}: {item.get('tool_hint')}")
    return "\n".join(lines)


def _looks_like_wait_for_system_exit(text: str) -> bool:
    """识别 Agent 以「等系统/等 4.2/4.3」收尾（易导致 stream 结束、发文未开）。"""
    try:
        from report_04.session_continue import looks_like_wait_exit

        return looks_like_wait_exit(text)
    except Exception:
        t = (text or "").strip()
        if not t:
            return False
        markers = (
            "等待系统完成",
            "等待系统收口",
            "等待系统推进",
            "等待系统",
            "会话保持中",
            "等待 4.2",
            "等待4.2",
            "等待步骤6",
            "后进入步骤4.3",
            "后进入步骤7",
            "后再进入步骤7",
            "后再采发文",
        )
        return any(m in t for m in markers)


def _early_exit_before_posts_message(task_id: str) -> Optional[str]:
    """会话结束时：门禁已开但发文未尝试 → 明确失败文案。"""
    if not can_run_step7_collect(task_id):
        return None
    try:
        from report_04.step_reconcile import list_unattempted_post_platforms

        todo = list_unattempted_post_platforms(task_id)
    except Exception:
        return None
    if not todo:
        return None
    plats = ", ".join(str(i.get("platform") or "") for i in todo[:8] if i.get("platform"))
    return (
        f"会话结束：步骤7发文已开放但未调用即结束（未尝试 {len(todo)} 个平台"
        + (f"：{plats}" if plats else "")
        + "）。禁止「等待系统」后结束会话；须同会话立刻调发文工具。"
    )


def _vision_settle_ready(task_id: str) -> bool:
    """图片流全部终态后立即可收口，不再等 quiet period。"""
    from report_04.gates import count_image_streams

    if not is_stream_compare_ready(task_id):
        return False
    if count_image_streams(task_id) == 0:
        return True
    return True


def _extra(payload: Dict[str, Any]) -> Dict[str, Any]:
    ex = payload.get("extra")
    return ex if isinstance(ex, dict) else {}


def _store() -> TaskStore:
    return TaskStore()


def _java_task_id_from_session_key(payload: Dict[str, Any]) -> Optional[str]:
    ex = _extra(payload)
    for key in ("gateway_session_key", "session_key"):
        raw = str(ex.get(key) or "").strip()
        if raw.startswith("task:"):
            tid = raw[5:].strip()
            if tid:
                return tid
    return None


def _resolve_task_id(payload: Dict[str, Any], user_message: str = "") -> Optional[str]:
    ex = _extra(payload)
    session_id = str(payload.get("session_id") or "").strip()
    store = _store()

    java_tid = _java_task_id_from_session_key(payload)
    if java_tid:
        row = store.get_task(java_tid)
        if row and str(row.get("task_type") or "") == "account_report":
            if session_id:
                store.bind_session(java_tid, session_id)
            return java_tid
        msg = (user_message or str(ex.get("user_message") or "")).strip()
        if msg and is_report_intent(msg):
            return store.ensure_task(session_id=session_id, user_message=msg, task_id=java_tid)
        if row and str(row.get("task_type") or "") == "account_report":
            if session_id:
                store.bind_session(java_tid, session_id)
            return java_tid

    if session_id:
        active = store.get_active_task_by_session(session_id)
        if active:
            return active["task_id"]
        # 主任务已 completed 时：续跑/Hook 重放仍应落在同 session 原任务，禁止 ensure_task 新建幽灵
        latest = store.get_latest_task_by_session(session_id)
        if latest and str(latest.get("task_type") or "") == "account_report":
            return str(latest["task_id"])

    tid = str(ex.get("task_id") or "").strip()
    if tid:
        row = store.get_task(tid)
        if row and str(row.get("task_type") or "") == "account_report":
            if session_id and row.get("session_id") != session_id:
                store.bind_session(tid, session_id)
            return tid

    msg = (user_message or str(ex.get("user_message") or "")).strip()
    if msg and is_report_intent(msg):
        return store.ensure_task(session_id=session_id, user_message=msg)
    return None


def _prepare_step4_collect(store: TaskStore, task_id: str) -> bool:
    """步骤二结束后可物化步骤四；若步骤3未收口则先收口。"""
    if get_step_status(task_id, "step2_maigret") not in {"completed", "skipped"}:
        return False
    if get_step_status(task_id, "step3_web_search") not in {"completed", "skipped"}:
        _close_step3_before_step4_tool(store, task_id)
    if not can_update_step4_children(task_id):
        return False
    store.materialize_step4_from_candidates(task_id)
    # 首个主页工具到来：点亮父步骤四
    if get_step_status(task_id, "step4_profiles") in {"pending", None}:
        store.set_step_status(task_id, "step4_profiles", "running", message="候选主页采集中")
    return True


def _resolve_collect_phase(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    platform: Optional[str],
    *,
    seed_collect: bool,
) -> tuple[Optional[str], str]:
    if seed_collect:
        return None, "step1_seed"
    if tool_name in WEB_SEARCH_TOOLS:
        return None, "step3_web_search"
    if tool_name == "mcp_maigret_collect_accounts":
        return None, "step2_maigret"
    if tool_name.startswith("mcp_maigret_"):
        return None, "step2_maigret"
    if tool_name in _STEP5_STREAM_TOOLS:
        sk = tool_step_key(tool_name)
        return None, sk
    try:
        from report_04.osint_es import is_osint_es_tool

        if is_osint_es_tool(tool_name):
            return None, "step6_osint_es"
    except Exception:
        pass
    # 步骤七已可跑：Apify Actor/dataset / 发文 MCP 优先归 step7，禁止误进 step4 重开已完成主页
    apify_like = (
        tool_name in APIFY_POST_TOOLS
        or tool_name in {"mcp_apify_get_actor_run", "mcp_apify_get_dataset_items"}
    )
    if (
        platform
        and can_run_step7_collect(task_id)
        and (tool_name in POST_TOOLS or apify_like)
    ):
        # 首个发文工具才点亮步骤7父节点（避免步骤5 vision 未停就假 running）
        store.start_step7_if_ready(task_id)
        if tool_name == "mcp_apify_get_dataset_items":
            cs = _resolve_dataset_collect_phase(task_id, platform)
        else:
            cs = post_platform_step_key(platform)
        return cs, cs
    if tool_name in STEP4_COLLECT_TOOLS or (tool_name == "mcp_apify_get_dataset_items" and platform):
        if platform and get_step_status(task_id, profile_platform_step_key(platform)) == "skipped":
            return None, "step4_profiles"
        _prepare_step4_collect(store, task_id)
        if platform and can_update_step4_children(task_id):
            if tool_name == "mcp_apify_get_dataset_items":
                cs = _resolve_dataset_collect_phase(task_id, platform)
            else:
                cs = tool_collect_step_key(tool_name, platform)
            return cs, cs
        return None, "step4_profiles"
    if tool_name in POST_TOOLS or (
        tool_name == "mcp_apify_get_dataset_items"
        and platform
        and step7_collect_active(task_id)
    ):
        if not can_run_step7_collect(task_id):
            # 步骤六完成前：仅记录 tool_outputs（phase 挂 step5，避免步骤树/前端误判进步骤7）
            return None, "step5_streams"
        store.start_step7_if_ready(task_id)
        if platform:
            cs = post_platform_step_key(platform)
            return cs, cs
    if platform:
        plat_step = tool_collect_step_key(tool_name, platform)
        if plat_step.startswith("step4_profile_") and not can_update_step4_children(task_id):
            return None, tool_step_key(tool_name)
        if plat_step.startswith("step7_post_") and not can_update_step7_children(task_id):
            return None, tool_step_key(tool_name)
        return plat_step, plat_step
    return None, tool_step_key(tool_name)


def _task_seed_platform(task_id: str) -> str:
    row = _store().get_task(task_id) or {}
    try:
        seed = json.loads(row.get("seed_json") or "{}")
    except Exception:
        seed = {}
    return str(seed.get("platform") or "twitter").lower()


def _is_seed_profile_tool(tool_name: str, task_id: str) -> bool:
    if get_step_status(task_id, "step1_seed") in {"completed", "skipped", "failed"}:
        return False
    if tool_name not in SEED_PROFILE_TOOLS:
        return False
    seed_plat = _task_seed_platform(task_id)
    # MCP 种子主页
    if tool_name in TOOL_TO_SEED_PLATFORM and not tool_name.startswith("mcp_apify_"):
        return True
    # Apify 种子：仅 seed_json.platform 为 Apify 平台时才绑定 step1
    if not is_apify_seed_platform(seed_plat):
        return False
    expected = APIFY_SEED_PLATFORM_TOOLS.get(seed_plat)
    if tool_name == expected:
        return True
    if tool_name in {"mcp_apify_get_actor_run", "mcp_apify_get_dataset_items"}:
        return True
    return False


def _strip_apify_posts_for_seed_phase(result_data: Dict[str, Any]) -> Dict[str, Any]:
    """Apify dataset 常夹带 posts；种子步只入库 profile。"""
    out = dict(result_data or {})
    out["posts"] = []
    return out


def _seed_fail_message(tool_name: str, tool_output: str, *, empty: bool = False) -> str:
    """生成种子失败文案，便于前端提示用户检查账号名。"""
    text = (tool_output or "").lower()
    if empty:
        # Apify 常返回空壳 profile（cleanItemCount=0），未必是账号不存在
        if "mcp_apify" in (tool_name or "") or "dataset" in text or '"type": "profile"' in text:
            return "种子主页 Apify 未返回有效资料（空结果），请稍后重试或核对账号名"
        return "种子主页采集无结果，请检查账号名是否正确后重试"
    if "does not exist" in text or "user not found" in text or "not found" in text:
        return "种子账号不存在（平台未找到），请检查账号名后重试"
    if "error" in text:
        # 截取可读片段
        snippet = (tool_output or "").replace("\n", " ").strip()
        if len(snippet) > 180:
            snippet = snippet[:180] + "…"
        return f"种子主页采集失败：{snippet}"
    return f"种子主页采集失败（{tool_name}），请检查账号名后重试"


# 种子失败重试：计数写在 step1 payload（db_sink 每次新进程，不能用内存计数）
_SEED_RETRY_MAX = 3
_SEED_FAIL_COUNT_KEY = "seed_fail_count"
_HARD_SEED_MARKERS = (
    "does not exist",
    "user not found",
    "account not found",
    "no such user",
    "could not find user",
    "账号不存在",
    "validation error",
    "requires either",
)


def _is_hard_seed_error(text: str) -> bool:
    """账号不存在等硬错误：不占重试，立刻 abort。"""
    low = (text or "").lower()
    if any(m in low for m in _HARD_SEED_MARKERS):
        return True
    # 泛 not found 视为硬错误，但排除 twikit ClientTransaction
    if "clienttransaction" in low:
        return False
    if re.search(r"\bnot found\b", low):
        return True
    return False


def _seed_fail_count(task_id: str) -> int:
    from collect_01 import db as _db

    row = _db.fetch_one(
        "SELECT payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, "step1_seed"),
    ) or {}
    raw = row.get("payload_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return 0
    try:
        return int(raw.get(_SEED_FAIL_COUNT_KEY) or 0)
    except Exception:
        return 0


def _clear_seed_fail_count(store: TaskStore, task_id: str) -> None:
    if _seed_fail_count(task_id) <= 0:
        return
    cur = get_step_status(task_id, "step1_seed") or "running"
    if cur in {"failed", "skipped"}:
        return
    store.set_step_status(
        task_id,
        "step1_seed",
        cur if cur in {"pending", "running", "completed"} else "running",
        payload={_SEED_FAIL_COUNT_KEY: 0},
    )


def _abort_or_retry_seed(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    fail_msg: str,
    raw_output: str,
) -> None:
    """种子失败：硬错误或满 3 次 → fail_seed_and_abort；否则 step1 保持 running。"""
    combined = f"{raw_output or ''}\n{fail_msg or ''}"
    if _is_hard_seed_error(combined):
        store.fail_seed_and_abort(task_id, fail_msg)
        logger.warning(
            "种子采集硬失败，立即中止 task=%s tool=%s: %s",
            task_id,
            tool_name,
            fail_msg[:180],
        )
        return
    n = _seed_fail_count(task_id) + 1
    if n >= _SEED_RETRY_MAX:
        store.fail_seed_and_abort(task_id, fail_msg)
        logger.warning(
            "种子采集失败已满 %s 次，中止 task=%s tool=%s: %s",
            _SEED_RETRY_MAX,
            task_id,
            tool_name,
            fail_msg[:180],
        )
        return
    store.set_step_status(
        task_id,
        "step1_seed",
        "running",
        message=f"种子采集失败（第{n}/{_SEED_RETRY_MAX}次），等待重试…",
        payload={
            _SEED_FAIL_COUNT_KEY: n,
            "seed_fail_last": (fail_msg or "")[:200],
        },
    )
    logger.warning(
        "种子采集瞬态失败，软重试 %s/%s task=%s tool=%s: %s",
        n,
        _SEED_RETRY_MAX,
        task_id,
        tool_name,
        fail_msg[:180],
    )


def _task_is_terminal(store: TaskStore, task_id: str) -> bool:
    task = store.get_task(task_id) or {}
    # cancelled：用户结束，禁止 Hook 继续推进写报（勿覆盖为 completed/running）
    return str(task.get("status") or "") in {"failed", "completed", "cancelled"}


def _maybe_stale_step5(store: TaskStore, task_id: str) -> None:
    """步骤五空等 Vision 超时则 fail-forward（会话中途也能解开）。"""
    try:
        from report_04.step_reconcile import maybe_fail_forward_stale_step5

        n = maybe_fail_forward_stale_step5(store, task_id, min_wait_seconds=45)
        if n:
            _maybe_advance_step67(store, task_id)
    except Exception as exc:
        logger.warning("step5 超时兜底失败 task=%s: %s", task_id, exc)


def _on_pre_tool(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """硬拦截：非法 YouTube channelId；步骤4未终态禁止 vision；步骤4后禁止无关 web；步骤5/6 未完成禁止发文；编排白名单。"""
    tool_name = _normalize_hook_tool_name(payload.get("tool_name"))
    if not tool_name or tool_name in _SKIP_STEP_TOOLS:
        return None
    try:
        task_id = _resolve_task_id(payload)
        if not task_id:
            return None
        tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        phase = None
        try:
            ex = _extra(payload)
            phase = str(ex.get("phase") or payload.get("phase") or "").strip() or None
        except Exception:
            phase = None
        # [COLLISION_DEMO_FAKE] 禁止 Agent 以假节点为 phase 调工具 — 正式版删除本段
        try:
            from report_04.collision_demo_steps import block_agent_tool_for_demo_step

            demo_reason = block_agent_tool_for_demo_step(phase, None)
            if demo_reason:
                logger.warning(
                    "[COLLISION_DEMO_FAKE] 拦截工具 task=%s tool=%s: %s",
                    task_id,
                    tool_name,
                    demo_reason[:160],
                )
                return {
                    "decision": "block",
                    "reason": demo_reason,
                }
        except Exception:
            pass
        # [COLLISION_DEMO_FAKE] end
        reason = (
            _is_skipped_step_tool(tool_name, task_id, tool_args=tool_args, phase=phase)
            or _is_invalid_youtube_channel_id(tool_name, tool_args)
            or _is_premature_step5_tool(tool_name, task_id)
            or _is_redundant_step5_vision(tool_name, tool_args, task_id)
            or _is_late_web_search_tool(tool_name, task_id)
            or _is_premature_osint_tool(tool_name, task_id)
            or _is_premature_step7_tool(tool_name, task_id)
        )
        already_enriched = False
        # 步骤7 有尚未尝试平台：非发文工具优先拦截并点名精确工具（比通用白名单更可执行）
        if not reason:
            try:
                from report_04.gates import can_run_step7_collect
                from report_04.phases import APIFY_POST_TOOLS, POST_TOOLS
                from report_04.step_reconcile import list_unattempted_post_platforms

                if can_run_step7_collect(task_id):
                    todo = list_unattempted_post_platforms(task_id)
                    post_ok = (
                        tool_name in POST_TOOLS
                        or tool_name in APIFY_POST_TOOLS
                        or tool_name
                        in {
                            "mcp_apify_get_actor_run",
                            "mcp_apify_get_dataset_items",
                        }
                    )
                    if todo and not post_ok:
                        lines = [
                            f"步骤7尚有 {len(todo)} 个 validated 平台未尝试发文工具，"
                            f"禁止调用 {tool_name}。本回合必须先调发文工具："
                        ]
                        for item in todo[:10]:
                            lines.append(
                                f"- {item.get('platform')}: {item.get('tool_hint')}"
                            )
                        reason = "\n".join(lines)
            except Exception:
                pass
        # 编排白名单（含步骤7仅允许发文工具）
        if not reason:
            try:
                from report_04.engine import pre_tool_allowed

                reason = pre_tool_allowed(task_id, tool_name, phase=phase)
                already_enriched = bool(reason)
            except Exception as exc:
                logger.warning("pre_tool_allowed 失败 task=%s: %s", task_id, exc)
        if not reason:
            return None
        if not already_enriched:
            try:
                from report_04.engine import enrich_block_reason

                reason = enrich_block_reason(task_id, reason)
            except Exception:
                pass
        try:
            from report_04.step_reconcile import ensure_step7_parent_not_premature

            ensure_step7_parent_not_premature(_store(), task_id)
        except Exception:
            pass
        logger.warning("拦截越序工具 task=%s tool=%s: %s", task_id, tool_name, reason[:200])
        return {"decision": "block", "reason": reason}
    except DbError as exc:
        logger.warning("pre_tool 门禁失败: %s", exc)
        return None


def _on_pre_llm(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ex = _extra(payload)
    user_message = str(ex.get("user_message") or "").strip()
    context: Optional[str] = None
    try:
        task_id = _resolve_task_id(payload, user_message=user_message)
        if not task_id:
            return None
        store = _store()
        session_id = str(payload.get("session_id") or "").strip()
        if session_id:
            store.bind_session(task_id, session_id)
        _maybe_stale_step5(store, task_id)
        _try_complete_step3_if_quiet(store, task_id)
        _try_complete_step5_if_settled(store, task_id)
        try:
            from report_04.engine import build_agent_context, run_pre_llm_auto

            run_pre_llm_auto(store, task_id)
        except Exception as exc:
            logger.warning("engine pre_llm 失败 task=%s: %s", task_id, exc)
        try:
            from report_04.step_reconcile import ensure_step7_parent_not_premature

            ensure_step7_parent_not_premature(store, task_id)
        except Exception:
            pass
        if user_message and is_report_intent(user_message):
            _ensure_seed_from_dialogue(store, task_id, user_message=user_message, session_id=session_id)
            if get_step_status(task_id, "step1_seed") == "pending":
                store.set_step_status(task_id, "step1_seed", "running", message="等待 Agent 确认种子账号…")
            if ex.get("is_first_turn") and not store.has_user_dialogue(task_id, "user_input"):
                store.save_dialogue(task_id, session_id, "user", user_message, "user_input")
        context = _step5_guidance_context(task_id)
        step7_ctx = _step7_ready_guidance(task_id)
        if step7_ctx:
            context = (context + "\n\n" + step7_ctx) if context else step7_ctx
        try:
            from report_04.engine import build_agent_context

            engine_ctx = build_agent_context(task_id)
            if engine_ctx:
                context = (context + "\n\n" + engine_ctx) if context else engine_ctx
        except Exception:
            pass
    except DbError as exc:
        logger.warning("%s", exc)
    if context:
        return {"context": context}
    return None


def _enter_step5(store: TaskStore, task_id: str) -> None:
    gate = can_advance_to_step5(task_id)
    if not gate.get("ok"):
        logger.info("进入 step5 跳过 task=%s: %s", task_id, gate.get("message"))
        return
    from report_04.stream_steps import ensure_stream_child_steps, start_step5_image_pipeline

    ensure_stream_child_steps(store, task_id)
    if get_step_status(task_id, "step5_streams") == "pending":
        store.set_step_status(task_id, "step5_streams", "running", message="文本/图片流核查中")
    store.kickoff_step5_if_ready(task_id)
    start_step5_image_pipeline(store, task_id)


def _prepare_enter_step5(store: TaskStore, task_id: str) -> None:
    """步骤五工具到来时：仅当步骤4已终态才进入；不再 force 跳过未采平台。"""
    _enter_step5(store, task_id)


def _maybe_advance_step67(store: TaskStore, task_id: str) -> None:
    """仅在步骤5父壳 completed 后推进步骤6；步骤7父节点等发文工具再点亮。"""
    from report_04.stream_steps import rollup_step5_parent

    gate5 = can_advance_to_step5(task_id)
    if not gate5.get("ok"):
        return
    if get_step_status(task_id, "step5_streams") != "completed":
        store.kickoff_step5_if_ready(task_id)
        rollup_step5_parent(store, task_id)
    if (
        get_step_status(task_id, "step5_streams") == "completed"
        and get_step_status(task_id, "step6_validated") != "completed"
    ):
        store.run_validated_accounts(task_id)


def _try_complete_step5_if_settled(store: TaskStore, task_id: str) -> bool:
    """兼容旧调用：改为子节点 rollup（4.1.2 由图片管线收口，不再等 vision settle）。"""
    from report_04.stream_steps import rollup_step5_parent

    if not can_advance_to_step5(task_id).get("ok"):
        return False
    return rollup_step5_parent(store, task_id)


def _on_step5_tool_after(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    *,
    success: bool,
) -> None:
    """Vision/OCR 仍可更新身份流；4.1.2 收口改由 image_pipeline，此处只 rollup。"""
    from report_04.stream_steps import rollup_step5_parent

    if tool_name in _STEP5_STREAM_TOOLS and get_step_status(task_id, "step5_streams") in {
        "completed",
        "skipped",
    }:
        logger.info("步骤5已收口，忽略迟到 vision/OCR task=%s tool=%s", task_id, tool_name)
        return

    if tool_name in _STEP5_STREAM_TOOLS:
        matched = bool(store.mark_image_stream_progress(task_id, tool_name, tool_args, success=success))
        if tool_name in {"mcp_vision_analyze", "vision_analyze"} and not matched:
            logger.warning("step5 vision 未匹配库内图片流 task=%s", task_id)

    gate = can_advance_to_step5(task_id)
    if not gate.get("ok"):
        return
    _enter_step5(store, task_id)
    rollup_step5_parent(store, task_id)
    if get_step_status(task_id, "step5_streams") == "completed":
        _maybe_advance_step67(store, task_id)


def _try_parse_seed(store: TaskStore, task_id: str, assistant: str, user_message: str) -> None:
    if get_step_status(task_id, "step1_seed") == "completed":
        return
    if not looks_like_seed_done(assistant) and not assistant.strip():
        return
    accounts = parse_seed_from_assistant(user_message=user_message, assistant_text=assistant)
    if not accounts:
        return
    store.mark_seed_completed(task_id, accounts, source="agent")
    logger.info("步骤1 解析账号 task=%s count=%d", task_id, len(accounts))


def _ensure_seed_from_dialogue(
    store: TaskStore,
    task_id: str,
    *,
    user_message: str = "",
    session_id: Optional[str] = None,
) -> None:
    """post_llm 未触发时：从 Agent 表格或用户原始输入兜底完成步骤一。"""
    if get_step_status(task_id, "step1_seed") == "completed":
        return
    assistant = _load_assistant_for_seed(session_id or "")
    if assistant and looks_like_seed_done(assistant):
        accounts = parse_seed_from_assistant(user_message=user_message, assistant_text=assistant)
        if accounts:
            store.mark_seed_completed(task_id, accounts, source="agent")
            logger.info("步骤1 兜底(assistant) task=%s count=%d", task_id, len(accounts))
            return
    msg = user_message.strip() or store.load_user_input_message(task_id)
    accounts = parse_seed_from_assistant(user_message=msg, assistant_text="")
    if accounts:
        store.mark_seed_completed(task_id, accounts, source="user_input")
        logger.info("步骤1 兜底(user_input) task=%s count=%d", task_id, len(accounts))


def _load_assistant_for_seed(session_id: str) -> str:
    if not session_id:
        return ""
    db_path = hermes_home() / "state.db"
    if not db_path.is_file():
        return ""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT content FROM messages
            WHERE session_id=? AND role='assistant'
              AND content IS NOT NULL AND TRIM(content) != ''
              AND TRIM(content) != '(empty)'
            ORDER BY id ASC
            LIMIT 20
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        text = str(row[0] or "").strip()
        if looks_like_seed_done(text):
            return text
    return ""


def _apply_skip_steps(store: TaskStore, task_id: str, text: str) -> None:
    for step_key in detect_skip_steps(text):
        if get_step_status(task_id, step_key) not in {"completed", "skipped"}:
            store.set_step_status(task_id, step_key, "skipped", message="单平台路径跳过")


def _mark_analysis_running(store: TaskStore, task_id: str) -> None:
    try:
        from report_04.step_reconcile import (
            reconcile_step4_and_step7_children,
            reconcile_step7_from_post_tools,
        )

        reconcile_step7_from_post_tools(store, task_id)
        reconcile_step4_and_step7_children(store, task_id)
        store.reconcile_collect_child_steps(task_id)
    except Exception as exc:
        logger.warning("步骤七收口失败 task=%s: %s", task_id, exc)
    if not can_advance_to_analysis(task_id).get("ok"):
        return
    for step_key in ANALYSIS_STEP_KEYS:
        if get_step_status(task_id, step_key) == "pending":
            store.set_step_status(task_id, step_key, "running", message="分析进行中…")


def _step3_threshold_met(task_id: str) -> tuple[bool, int, int, int]:
    from collect_01 import db as _db

    row = _db.fetch_one(
        """
        SELECT
          SUM(tool_name='web_search' AND status='success') AS n_search,
          SUM(tool_name='web_extract' AND status='success') AS n_extract,
          SUM(tool_name LIKE 'browser_%%' AND status='success') AS n_browser
        FROM hermes_tool_outputs
        WHERE task_id=%s AND phase='step3_web_search'
        """,
        (task_id,),
    )
    n_search = int((row or {}).get("n_search") or 0)
    n_extract = int((row or {}).get("n_extract") or 0)
    n_browser = int((row or {}).get("n_browser") or 0)
    ok = (
        n_extract >= 1
        or n_browser >= 1
        or n_search >= int(_STEP3_MIN_SEARCH_FOR_DONE)
    )
    return ok, n_search, n_extract, n_browser


def _step3_web_tool_counts(task_id: str) -> tuple[int, int]:
    """返回 (web_search成功数, 全部WEB工具成功数)。"""
    from collect_01 import db as _db

    row = _db.fetch_one(
        """
        SELECT
          SUM(tool_name='web_search' AND status='success') AS n_search,
          SUM(status='success') AS n_all
        FROM hermes_tool_outputs
        WHERE task_id=%s AND phase='step3_web_search'
          AND tool_name IN ('web_search','web_extract',
            'browser_navigate','browser_vision','browser_click','browser_type')
        """,
        (task_id,),
    )
    return int((row or {}).get("n_search") or 0), int((row or {}).get("n_all") or 0)


def _step3_wall_age_seconds(task_id: str) -> Optional[float]:
    """步骤3 running 起算的墙钟秒数；无 started_at 则用首条 web 工具时间。"""
    from datetime import datetime

    from collect_01 import db as _db

    row = _db.fetch_one(
        """
        SELECT started_at, status FROM collect_phase_steps
        WHERE task_id=%s AND step_key='step3_web_search'
        """,
        (task_id,),
    )
    started = (row or {}).get("started_at")
    if started is None:
        first = _db.fetch_one(
            """
            SELECT MIN(executed_at) AS first_at FROM hermes_tool_outputs
            WHERE task_id=%s AND phase='step3_web_search' AND status='success'
            """,
            (task_id,),
        )
        started = (first or {}).get("first_at")
    if started is None:
        return None
    if hasattr(started, "timestamp"):
        try:
            return max(0.0, (datetime.now() - started).total_seconds())
        except Exception:
            return None
    return None


def step3_web_budget_exhausted(task_id: str) -> Optional[str]:
    """步骤3预算耗尽原因；None 表示仍可继续检索。"""
    if get_step_status(task_id, "step3_web_search") in {"completed", "skipped"}:
        return "already_done"
    wall = _step3_wall_age_seconds(task_id)
    if wall is not None and wall >= float(_STEP3_MAX_WALL_SECONDS):
        return f"墙钟已达 {int(_STEP3_MAX_WALL_SECONDS)}s"
    n_search, n_all = _step3_web_tool_counts(task_id)
    if n_search >= int(_STEP3_MAX_WEB_SEARCH):
        return f"web_search 已达上限 {_STEP3_MAX_WEB_SEARCH} 次"
    if n_all >= int(_STEP3_MAX_WEB_TOOLS):
        return f"网页检索工具已达上限 {_STEP3_MAX_WEB_TOOLS} 次"
    return None


def _complete_step3_now(
    store: TaskStore,
    task_id: str,
    *,
    n_search: int,
    n_extract: int,
    message: Optional[str] = None,
) -> None:
    from collect_01 import db as _db

    n_web_cands = _db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM cross_platform_candidates
        WHERE task_id=%s AND match_strategy='web_search'
        """,
        (task_id,),
    )
    n_web = int((n_web_cands or {}).get("c") or 0)
    msg = message or f"网页检索完成（search={n_search} extract={n_extract} 候选={n_web}）"
    store.set_step_status(
        task_id,
        "step3_web_search",
        "completed",
        message=msg,
    )
    store.materialize_step4_from_candidates(task_id)


def _force_complete_step3_by_budget(store: TaskStore, task_id: str) -> bool:
    """墙钟/次数耗尽时强制收口步骤3，进入步骤4。"""
    if not can_run_step3_web_search(task_id):
        return False
    if get_step_status(task_id, "step3_web_search") in {"completed", "skipped"}:
        return False
    reason = step3_web_budget_exhausted(task_id)
    if not reason or reason == "already_done":
        return False
    ok, n_search, n_extract, _n_browser = _step3_threshold_met(task_id)
    _ = ok
    _complete_step3_now(
        store,
        task_id,
        n_search=n_search,
        n_extract=n_extract,
        message=f"网页检索超时/达上限收口（{reason}；search={n_search} extract={n_extract}）",
    )
    logger.info(
        "step3 预算耗尽强制收口 task=%s reason=%s search=%s extract=%s",
        task_id,
        reason,
        n_search,
        n_extract,
    )
    return True


def _try_complete_step3_if_quiet(store: TaskStore, task_id: str) -> bool:
    """检索安静期过后才收口步骤3；预算耗尽则立刻收口。"""
    if not can_run_step3_web_search(task_id):
        return False
    if get_step_status(task_id, "step3_web_search") in {"completed", "skipped"}:
        return False
    if _force_complete_step3_by_budget(store, task_id):
        return True
    ok, n_search, n_extract, _n_browser = _step3_threshold_met(task_id)
    if not ok:
        return False
    age = seconds_since_last_tool(task_id, phase_prefix="step3_web_search")
    if age is None or age < float(_STEP3_QUIET_SECONDS):
        if get_step_status(task_id, "step3_web_search") == "pending":
            store.set_step_status(
                task_id,
                "step3_web_search",
                "running",
                message="网页检索中，等待本轮检索结束…",
            )
        return False
    _complete_step3_now(store, task_id, n_search=n_search, n_extract=n_extract)
    return True


def _maybe_complete_step3_after_web_tool(
    store: TaskStore,
    task_id: str,
    tool_name: str,
) -> None:
    """步骤三：web 工具成功后解析候选；安静期或预算耗尽后收口。"""
    if tool_name not in WEB_SEARCH_TOOLS:
        return
    if not can_run_step3_web_search(task_id):
        return
    if get_step_status(task_id, "step3_web_search") in {"completed", "skipped"}:
        return
    if get_step_status(task_id, "step3_web_search") == "pending":
        store.set_step_status(task_id, "step3_web_search", "running", message=f"网页检索中 ({tool_name})")
    _try_complete_step3_if_quiet(store, task_id)


def _close_step3_before_step4_tool(store: TaskStore, task_id: str) -> None:
    """首个步骤4主页工具到来时，强制收口步骤3。"""
    if get_step_status(task_id, "step3_web_search") in {"completed", "skipped"}:
        return
    if not can_run_step3_web_search(task_id):
        return
    ok, n_search, n_extract, _ = _step3_threshold_met(task_id)
    if not ok and n_search < 1 and n_extract < 1:
        # 无 web 结果也允许进入步骤4（仅种子候选）
        n_search = max(n_search, 0)
    _complete_step3_now(store, task_id, n_search=n_search, n_extract=n_extract)


def _save_step3_candidates_from_tool(
    store: TaskStore,
    task_id: str,
    tool_name: str,
    raw_output: Any,
) -> None:
    """从 web 工具原始输出解析并保存步骤三候选。"""
    if tool_name not in WEB_SEARCH_TOOLS:
        return
    if not can_run_step3_web_search(task_id):
        return
    candidates = parse_web_search_candidates_from_tool(tool_name, raw_output)
    if not candidates:
        return
    for row in candidates:
        row["task_id"] = task_id
        row.setdefault("match_strategy", "web_search")
    store.save_candidate_rows(candidates, step_key="step3_web_search")


def _try_step3_web_candidates(store: TaskStore, task_id: str, assistant: str) -> None:
    text = assistant or ""
    if not text:
        return
    # 终稿由 _complete_step11_from_report 收口 8～11，禁止再点亮分析步骤 running
    if is_final_report(text):
        _apply_skip_steps(store, task_id, text)
        return
    progress_text = is_progress_only(text)
    _apply_skip_steps(store, task_id, text)
    if progress_text and mentions_analysis_steps(text):
        _mark_analysis_running(store, task_id)
        return
    if not can_run_step3_web_search(task_id):
        return
    if looks_like_step3_summary(text):
        candidates = parse_web_search_candidates(text)
        for row in candidates:
            row["task_id"] = task_id
            row["match_strategy"] = row.get("discovery_source") or "web_search"
        if candidates:
            store.save_candidate_rows(candidates, step_key="step3_web_search")
        if get_step_status(task_id, "step3_web_search") not in {"completed", "skipped"}:
            store.set_step_status(
                task_id,
                "step3_web_search",
                "completed",
                message=f"网页检索候选 {len(candidates)} 个",
            )
            store.materialize_step4_from_candidates(task_id)
    if progress_text:
        return
    blocks = parse_standalone_analysis_blocks(text)
    analysis_ok = can_advance_to_analysis(task_id).get("ok")
    for step_key, content in blocks.items():
        if not analysis_ok:
            logger.info("步骤7未收口，暂不写分析块 task=%s step=%s", task_id, step_key)
            continue
        if get_step_status(task_id, step_key) not in {"completed", "skipped"}:
            store.save_analysis_display(task_id, step_key, content, source="standalone")
    if mentions_analysis_steps(text):
        _mark_analysis_running(store, task_id)


def _looks_like_report_meta_closing(text: str) -> bool:
    """终稿后的短收尾（「报告已完成」），本身不是 summary 正文。"""
    t = (text or "").strip()
    if not t or is_final_report(t):
        return False
    if len(t) > 400:
        return False
    markers = ("报告已完成", "画像报告已完成", "全部11个步骤", "四个章节", "完整画像报告")
    return any(m in t for m in markers)


def _resolve_final_report_text(assistant: str, session_id: Optional[str]) -> Optional[str]:
    """当前轮是终稿则用当前轮；否则从 state.db 回扫真终稿（避免短收尾覆盖）。

    返回已剥前缀并清洗脏行的正文，便于落 summary / 回填。
    """
    from report_04.report_parser import prepare_final_report_body

    text = (assistant or "").strip()
    if is_final_report(text):
        return prepare_final_report_body(text)
    from_state = _load_assistant_output_from_state(session_id or "")
    if from_state and is_final_report(from_state):
        return prepare_final_report_body(from_state)
    if text and not _looks_like_report_meta_closing(text):
        return None
    if from_state and is_final_report(from_state):
        return prepare_final_report_body(from_state)
    return from_state


def _fail_forward_contaminated_report(
    store: TaskStore,
    task_id: str,
    *,
    reason: str = "终稿含管线元叙述，清洗后仍不合格",
) -> None:
    """拒收脏终稿时禁止 8～10/11 停在 running：分析跳过、报告失败、任务 failed。"""
    for step_key in ANALYSIS_STEP_KEYS:
        st = get_step_status(task_id, step_key)
        if st not in {"completed", "skipped"}:
            store.set_step_status(
                task_id,
                step_key,
                "skipped",
                message=reason[:180],
            )
    try:
        store._maybe_complete_phase_shell(task_id, "step10_context_pii")
    except Exception as exc:
        logger.warning("脏终稿 fail-forward 收口 phase_analysis 失败 task=%s: %s", task_id, exc)
    if get_step_status(task_id, "step11_report") not in {"completed", "skipped", "failed"}:
        store.set_step_status(task_id, "step11_report", "failed", message=reason[:180])
    try:
        store._maybe_complete_phase_shell(task_id, "step11_report")
    except Exception as exc:
        logger.warning("脏终稿 fail-forward 收口 phase_report 失败 task=%s: %s", task_id, exc)
    try:
        store.mark_task_failed(task_id, reason[:500])
    except Exception as exc:
        logger.warning("脏终稿 fail-forward 标记任务失败异常 task=%s: %s", task_id, exc)
    logger.warning("脏终稿 fail-forward 完成 task=%s reason=%s", task_id, reason[:120])


def _complete_step11_from_report(
    store: TaskStore,
    task_id: str,
    assistant: str,
    session_id: Optional[str],
) -> None:
    """先轻量落 summary / 收口 8～11，再可选 backfill 与 reconcile（防 Hook 120s 超时半截）。"""
    from report_04.report_parser import (
        looks_like_report_attempt,
        prepare_final_report_body,
    )

    if get_step_status(task_id, "step11_report") == "completed":
        # 已收口 step11 但任务行可能仍 running（Hook 超时 / 续跑未补标）→ 补 completed
        try:
            from report_04.engine import _try_finalize_report

            _try_finalize_report(store, task_id, light_only=True)
        except Exception as exc:
            logger.warning("step11 已完成时补标 completed 失败 task=%s: %s", task_id, exc)
        return
    # 发文或视频未终态：不落 step11/summary，避免抢跑（也不 fail-forward）
    inject_fn = None
    try:
        from report_04.video_report import can_write_report_after_videos, inject_video_into_report

        inject_fn = inject_video_into_report
        gate = can_write_report_after_videos(task_id)
        if not gate.get("ok"):
            logger.info(
                "终稿等待发文/视频终态 task=%s open=%s",
                task_id,
                gate.get("open"),
            )
            return
    except Exception as exc:
        logger.warning("写报门禁检查失败，暂不收口 step11 task=%s: %s", task_id, exc)
        return

    if not is_final_report(assistant):
        if looks_like_report_attempt(assistant):
            _fail_forward_contaminated_report(store, task_id)
        return

    # 落库用剥前缀 + 清洗后的正文，脏行不进 summary
    report_body = prepare_final_report_body(assistant)
    if inject_fn is not None:
        try:
            report_body = inject_fn(report_body, task_id)
        except Exception as exc:
            logger.warning("终稿并入视频观察失败 task=%s: %s", task_id, exc)

    # —— 轻量路径：立刻终态，避免超时停在 running ——
    backfill = backfill_analysis_from_report(report_body)
    try:
        # 终稿已出时直接回填；此时 step7 可能尚未收口，不能再用 can_advance 挡住
        for step_key, content in backfill.items():
            if get_step_status(task_id, step_key) not in {"completed", "skipped"}:
                store.save_analysis_display(
                    task_id, step_key, content, source="backfill_from_step11"
                )
    except Exception as exc:
        logger.warning("终稿轻量 backfill 失败 task=%s: %s", task_id, exc)
    for step_key in ANALYSIS_STEP_KEYS:
        st = get_step_status(task_id, step_key)
        if st not in {"completed", "skipped"}:
            # pending/running 一律 skip，禁止停在 running
            store.set_step_status(task_id, step_key, "skipped", message="终稿已出，未单独输出")
    # 子步若先前已终态，上面会跳过 set_step_status，父壳 phase_analysis 可能仍 pending；强制 rollup
    try:
        store._maybe_complete_phase_shell(task_id, "step10_context_pii")
    except Exception as exc:
        logger.warning("终稿后 phase_analysis 收口失败 task=%s: %s", task_id, exc)
    store.save_assistant_output(task_id, session_id, report_body)
    store.set_step_status(task_id, "step11_report", "completed", message="画像报告已生成")
    from report_04.phases import PHASE_DONE

    store.set_task_phase(task_id, PHASE_DONE)
    logger.info("终稿轻量收口完成 task=%s summary_len=%d", task_id, len(report_body or ""))
    # 必须先于重路径标 completed：否则 Hook 超时/续跑占会话时任务会永久停在 running
    try:
        from report_04.engine import _try_finalize_report

        _try_finalize_report(store, task_id, light_only=True)
    except Exception as exc:
        logger.warning("终稿后任务 completed 标记失败 task=%s: %s", task_id, exc)

    # —— 重路径：步骤4/7 收口（失败不影响已落的 summary / completed）——
    # 终稿已出却留下 step7 子节点 pending → 父节点永久 running；必须先批量 skip 再关父节点
    try:
        from report_04.orchestrator import advance_to_analysis_phase
        from report_04.step_reconcile import (
            close_collect_parent_if_ready,
            reconcile_step4_and_step7_children,
            reconcile_step7_from_post_tools,
        )
        from report_04.phases import POST_PARENT_STEP_KEY
        from report_04.task_store import _reconcile_report_post_child_steps

        reconcile_step7_from_post_tools(store, task_id)
        reconcile_step4_and_step7_children(store, task_id)
        _reconcile_report_post_child_steps(store, task_id)
        advance_to_analysis_phase(
            store,
            task_id,
            "终稿已出，收口未完成的发文子步骤",
            force_skip_unattempted=True,
        )
        store.reconcile_collect_child_steps(task_id)
        close_collect_parent_if_ready(store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕")
    except Exception as exc:
        logger.warning("终稿后步骤七收口失败 task=%s: %s", task_id, exc)
    # 重路径可能改回 current_phase；再钉一次 done + completed
    try:
        store.set_task_phase(task_id, PHASE_DONE)
        from report_04.engine import _try_finalize_report

        _try_finalize_report(store, task_id, light_only=True)
    except Exception:
        pass


def _infer_platform(
    tool_name: str,
    tool_args: Dict[str, Any],
    accounts: List[Dict[str, Any]],
    platform_hint: str = "",
) -> Optional[str]:
    if tool_name in WEB_SEARCH_TOOLS or tool_name == "mcp_maigret_collect_accounts":
        return None
    plat = TOOL_PLATFORM.get(tool_name)
    if plat:
        return plat
    if tool_name == "mcp_apify_get_dataset_items" and platform_hint:
        for actor_tool, p in APIFY_TOOL_PLATFORM.items():
            actor_hint = actor_tool.replace("mcp_apify_", "")
            if platform_hint == actor_hint or p in platform_hint:
                return p
    blob = json.dumps(tool_args or {}, ensure_ascii=False).lower()
    for acc in accounts:
        handle = str(acc.get("account_handle") or acc.get("account_id") or "").lower()
        if handle and handle in blob:
            return str(acc.get("platform") or "")
    return None


def _resolve_dataset_collect_phase(task_id: str, platform: str) -> str:
    """步骤四拉主页 dataset vs 步骤七拉发文 dataset。"""
    prof_key = profile_platform_step_key(platform)
    if not can_run_step7_collect(task_id):
        return prof_key
    prof_st = get_step_status(task_id, prof_key)
    if prof_st in {"running", "pending"}:
        return prof_key
    return post_platform_step_key(platform)


def _sync_platform_collect_steps(
    store: TaskStore,
    task_id: str,
    platform: str,
    result_data: Dict[str, Any],
    *,
    tool_name: str,
    tool_ok: bool,
    tool_output: str = "",
    tool_args: Optional[Dict[str, Any]] = None,
) -> None:
    if not platform:
        return
    # 步骤七发文轮：只推进 step7_post_*，禁止把已完成的 step4_profile_* 打回 running
    in_step7 = can_run_step7_collect(task_id) and step7_collect_active(task_id)
    if not in_step7 and not can_update_step4_children(task_id):
        return
    prof_key = profile_platform_step_key(platform)
    post_key = post_platform_step_key(platform)
    if not in_step7:
        store.ensure_step_row(task_id, prof_key)

    n_prof = len(result_data.get("profiles") or [])
    n_post = len(result_data.get("posts") or [])
    if n_post == 0 and tool_name in POST_TOOLS and tool_ok and can_run_step7_collect(task_id):
        from collect_01 import db as _db

        row = _db.fetch_one(
            "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s AND platform=%s",
            (task_id, platform),
        )
        n_post = int((row or {}).get("c") or 0)

    apify_actor = bool(apify_platform_from_actor_tool(tool_name))

    if in_step7:
        store.ensure_step_row(task_id, post_key)
        if n_post > 0:
            from report_04.video_job import finalize_post_platform_after_posts

            cur_post = get_step_status(task_id, post_key)
            finalize_post_platform_after_posts(
                store,
                task_id,
                platform,
                post_count=n_post,
                force_reopen=(cur_post == "skipped"),
            )
            # 方案 A：发文子步一完成立刻尝试关父壳，不等本轮 LLM / Hook 收尾
            try:
                from report_04.step_reconcile import force_close_step7_posts_if_ready

                force_close_step7_posts_if_ready(store, task_id)
            except Exception as exc:
                logger.warning("发文入库后强制关 step7 失败 task=%s: %s", task_id, exc)
        elif apify_actor and tool_ok:
            cur = get_step_status(task_id, post_key)
            if cur not in {"completed", "skipped"}:
                # Actor 已报 itemCount=0：直接 skip，勿空等 get_dataset_items
                if actor_reported_empty_dataset(tool_output):
                    store.set_step_status(
                        task_id, post_key, "skipped", message=f"{platform} 未采集到发文"
                    )
                else:
                    store.set_step_status(
                        task_id, post_key, "running", message=f"{platform} Actor 已完成，等待发文 dataset…"
                    )
        elif tool_name == "mcp_apify_get_dataset_items" and tool_ok:
            cur = get_step_status(task_id, post_key)
            if n_post == 0 and cur not in {"completed", "skipped"}:
                n_prof = len(result_data.get("profiles") or [])
                raw_items = result_data.get("raw_item_count")
                if raw_items is None:
                    outcome = str(result_data.get("collect_outcome") or "")
                    raw_items = -1 if outcome not in {"empty", "not_found", ""} else 0
                raw_items = int(raw_items or 0)
                if platform == "facebook" and n_prof > 0 and raw_items > 0:
                    store.set_step_status(
                        task_id,
                        post_key,
                        "skipped",
                        message=f"{platform} Apify 仅返回主页，未采集到 PAGE 发帖",
                    )
                elif raw_items > 0:
                    store.set_step_status(
                        task_id,
                        post_key,
                        "running",
                        message=f"{platform} dataset 有 {raw_items} 条但未解析入库，待补字段或重拉",
                    )
                else:
                    store.set_step_status(task_id, post_key, "skipped", message=f"{platform} 未采集到发文")
        elif tool_name in POST_TOOLS:
            from report_04.step_reconcile import apply_step7_post_mcp_zero_outcome

            skipped = apply_step7_post_mcp_zero_outcome(
                store,
                task_id,
                platform,
                tool_ok=tool_ok,
                tool_output=tool_output,
                tool_name=tool_name,
            )
            if not skipped:
                cur = get_step_status(task_id, post_key)
                if cur == "pending":
                    store.set_step_status(
                        task_id, post_key, "running", message=f"{platform} 发文采集中…"
                    )
        store.reconcile_collect_child_steps(task_id)
        return

    if n_prof > 0:
        store.set_step_status(task_id, prof_key, "completed", message=f"已入库主页 {n_prof} 条")
    elif tool_name in PROFILE_TOOLS and not tool_ok:
        from collect_01 import db as _db

        row = _db.fetch_one(
            "SELECT COUNT(*) AS c FROM collect_profiles WHERE task_id=%s AND platform=%s",
            (task_id, platform),
        )
        has_prof = int((row or {}).get("c") or 0) > 0
        cur = get_step_status(task_id, prof_key)
        if has_prof:
            store.set_step_status(task_id, prof_key, "completed", message="已入库主页")
        elif cur not in {"completed", "skipped", "failed"}:
            terminal = _terminal_profile_failure_reason(
                tool_name, tool_output=tool_output, tool_args=tool_args or {}
            )
            if terminal:
                # 失败即跳过：正式 UC not found 等不可恢复错误，禁止长期 running 卡死步骤4
                store.set_step_status(task_id, prof_key, "skipped", message=terminal[:200])
            else:
                # 首次失败（如 YouTube @handle 待解析）保持 running，等待 channelId 重试
                store.set_step_status(
                    task_id, prof_key, "running", message=f"{platform} 主页采集中（等待重试）…"
                )
    elif tool_name in PROFILE_TOOLS:
        cur = get_step_status(task_id, prof_key)
        if apify_platform_from_actor_tool(tool_name) and tool_ok:
            if cur not in {"completed", "skipped", "failed"}:
                # Actor 已报 itemCount=0：直接 skip，勿空等 get_dataset_items 卡死步骤4
                if actor_reported_empty_dataset(tool_output):
                    store.set_step_status(
                        task_id,
                        prof_key,
                        "skipped",
                        message=apify_fail_message(platform, "empty"),
                    )
                else:
                    store.set_step_status(
                        task_id,
                        prof_key,
                        "running",
                        message=f"{platform} Actor 已完成，等待拉取 dataset…",
                    )
        elif cur == "pending":
            store.set_step_status(task_id, prof_key, "running", message=f"{platform} 主页采集中…")
    elif tool_name == "mcp_apify_get_dataset_items" and tool_ok and not can_run_step7_collect(task_id):
        cur = get_step_status(task_id, prof_key)
        if n_prof > 0 and cur != "completed":
            store.set_step_status(task_id, prof_key, "completed", message=f"已入库主页 {n_prof} 条")
        elif n_prof == 0 and cur == "running":
            from report_04.step_reconcile import _dataset_success_for_profile
            from collect_01.normalizers.apify import apify_fail_message

            if _dataset_success_for_profile(task_id, platform):
                outcome = str(result_data.get("collect_outcome") or "empty").strip().lower()
                store.set_step_status(
                    task_id,
                    prof_key,
                    "skipped",
                    message=apify_fail_message(platform, outcome),
                )

    if can_run_step7_collect(task_id):
        store.ensure_step_row(task_id, post_key)
        if n_post > 0:
            from report_04.video_job import finalize_post_platform_after_posts

            finalize_post_platform_after_posts(
                store, task_id, platform, post_count=n_post
            )
        elif tool_name in POST_TOOLS and tool_ok:
            cur = get_step_status(task_id, post_key)
            if n_post == 0 and tool_name == "mcp_apify_get_dataset_items":
                if cur not in {"completed", "skipped"}:
                    store.set_step_status(task_id, post_key, "skipped", message=f"{platform} 未采集到发文")
            else:
                from report_04.step_reconcile import apply_step7_post_mcp_zero_outcome

                skipped = apply_step7_post_mcp_zero_outcome(
                    store,
                    task_id,
                    platform,
                    tool_ok=True,
                    tool_output=tool_output,
                    tool_name=tool_name,
                )
                if not skipped and cur == "pending":
                    store.set_step_status(
                        task_id, post_key, "running", message=f"{platform} 发文采集中…"
                    )
        elif tool_name in POST_TOOLS and not tool_ok:
            from collect_01 import db as _db

            row = _db.fetch_one(
                "SELECT COUNT(*) AS c FROM collect_posts WHERE task_id=%s AND platform=%s",
                (task_id, platform),
            )
            has_post = int((row or {}).get("c") or 0) > 0
            cur = get_step_status(task_id, post_key)
            if has_post:
                from report_04.video_job import finalize_post_platform_after_posts

                finalize_post_platform_after_posts(
                    store, task_id, platform, post_count=int(row["c"])
                )
            else:
                from report_04.step_reconcile import apply_step7_post_mcp_zero_outcome

                skipped = apply_step7_post_mcp_zero_outcome(
                    store,
                    task_id,
                    platform,
                    tool_ok=False,
                    tool_output=tool_output,
                    tool_name=tool_name,
                )
                if not skipped and cur not in {"completed", "skipped"}:
                    store.set_step_status(
                        task_id, post_key, "running", message=f"{platform} 发文采集中（等待重试）…"
                    )
        elif tool_name in POST_TOOLS:
            if get_step_status(task_id, post_key) == "pending":
                store.set_step_status(task_id, post_key, "running", message=f"{platform} 发文采集中…")

    store.reconcile_collect_child_steps(task_id)


def _try_complete_profiles(store: TaskStore, task_id: str) -> None:
    try:
        from report_04.step_reconcile import maybe_close_abandoned_step4

        maybe_close_abandoned_step4(store, task_id)
    except Exception as exc:
        logger.warning("abandoned step4 收口失败 task=%s: %s", task_id, exc)
    store.reconcile_collect_child_steps(task_id)
    if get_step_status(task_id, "step4_profiles") in {"completed", "skipped"}:
        store.kickoff_step5_if_ready(task_id)


def _on_post_tool(payload: Dict[str, Any]) -> None:
    from collect_01.normalizers.base import normalize_mcp_tool_name

    tool_name = normalize_mcp_tool_name(str(payload.get("tool_name") or ""))
    if not tool_name or tool_name in _SKIP_STEP_TOOLS:
        return
    ex = _extra(payload)
    tool_call_id = str(ex.get("tool_call_id") or "").strip()
    if tool_call_id and tool_call_id in _SEEN_TOOL_CALLS:
        return

    user_message = str(ex.get("user_message") or "").strip()
    task_id = _resolve_task_id(payload, user_message=user_message)
    if not task_id:
        return

    store = _store()
    session_id = str(payload.get("session_id") or "").strip()

    # 任务已失败/完成：仅保留工具审计，不再推进任何步骤
    if _task_is_terminal(store, task_id):
        result = ex.get("result")
        if isinstance(result, (dict, list)):
            tool_output = json.dumps(result, ensure_ascii=False, default=str)
        else:
            tool_output = str(result or "")
        status = "success" if ex.get("status") == "ok" else "error"
        tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        try:
            store.save_tool_output(
                task_id=task_id,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_output=tool_output,
                tool_call_id=tool_call_id or f"anon-{tool_name}-{len(_SEEN_TOOL_CALLS)}",
                duration_ms=ex.get("duration_ms"),
                status=status,
                phase=tool_step_key(tool_name),
                mcp_server=infer_mcp_server(tool_name),
            )
        except DbError:
            pass
        if tool_call_id:
            _SEEN_TOOL_CALLS.add(tool_call_id)
        logger.info("任务已终态，忽略步骤推进 task=%s tool=%s", task_id, tool_name)
        return

    _ensure_seed_from_dialogue(store, task_id, user_message=user_message, session_id=session_id)

    tool_args = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    input_accounts = store.get_seed_accounts(task_id)
    platform_hint_early = _LAST_APIFY_HINT.get(task_id, "")
    platform = _infer_platform(tool_name, tool_args, input_accounts, platform_hint_early)
    seed_collect = _is_seed_profile_tool(tool_name, task_id)
    collect_step, output_step_key = _resolve_collect_phase(
        store, task_id, tool_name, platform, seed_collect=seed_collect
    )

    if seed_collect:
        if get_step_status(task_id, "step1_seed") not in {"completed", "failed", "skipped"}:
            store.set_step_status(task_id, "step1_seed", "running", message=f"种子采集中 ({tool_name})")
        # 粗同步可能误点 step4：种子轮立刻纠正
        try:
            from report_04.step_reconcile import ensure_step4_parent_not_premature

            ensure_step4_parent_not_premature(store, task_id)
        except Exception:
            pass
    else:
        primary_step = TOOL_PRIMARY_STEP.get(tool_name)
        # 步骤一已完成后，主页 MCP 应驱动 step4_profiles，而非再点 step1_seed
        if (
            primary_step == "step1_seed"
            and get_step_status(task_id, "step1_seed") in {"completed", "failed", "skipped"}
            and tool_name in PROFILE_TOOLS
        ):
            primary_step = "step4_profiles"
        if primary_step and tool_name not in _STEP5_STREAM_TOOLS:
            if primary_step == "step4_profiles" and not discovery_steps_terminal(task_id):
                pass
            elif primary_step == "step3_web_search" and not can_run_step3_web_search(task_id):
                pass
            elif primary_step == "step7_posts" and not can_run_step7_collect(task_id):
                pass
            elif primary_step == OSINT_ES_STEP_KEY and not can_advance_to_osint(task_id).get("ok"):
                # 禁止抢跑点亮 4.3（含连带 phase_collision 壳）
                pass
            else:
                cur = get_step_status(task_id, primary_step)
                if cur not in {"completed", "failed", "skipped"}:
                    store.set_step_status(task_id, primary_step, "running", message=f"执行 {tool_name}")
    if collect_step and not seed_collect:
        blocked = (
            (collect_step.startswith("step4_profile_") and not can_update_step4_children(task_id))
            or (collect_step.startswith("step7_post_") and not can_update_step7_children(task_id))
        )
        if not blocked:
            child_cur = get_step_status(task_id, collect_step)
            if child_cur not in {"completed", "failed", "skipped"}:
                kind = "发文" if collect_step.startswith("step7_post_") else "主页"
                store.set_step_status(task_id, collect_step, "running", message=f"{platform} {kind}采集中…")

    if tool_name in _STEP5_STREAM_TOOLS:
        _prepare_enter_step5(store, task_id)

    result = ex.get("result")
    if isinstance(result, (dict, list)):
        tool_output = json.dumps(result, ensure_ascii=False, default=str)
    else:
        tool_output = str(result or "")

    status = "success" if ex.get("status") == "ok" else "error"

    try:
        tool_output_id = store.save_tool_output(
            task_id=task_id,
            tool_name=tool_name,
            tool_args=tool_args,
            tool_output=tool_output,
            tool_call_id=tool_call_id or f"anon-{tool_name}-{len(_SEEN_TOOL_CALLS)}",
            duration_ms=ex.get("duration_ms"),
            status=status,
            phase=output_step_key,
            mcp_server=infer_mcp_server(tool_name),
        )
    except DbError as exc:
        logger.warning("写 tool_outputs 失败 tool=%s task=%s: %s", tool_name, task_id, exc)
        return

    if tool_call_id:
        _SEEN_TOOL_CALLS.add(tool_call_id)

    # 4.3 社工库：落薄表 + 点亮步骤，无 profile/post 产物
    try:
        from report_04.osint_es import (
            is_search_country_wise_tool,
            kickoff_osint_if_ready,
            maybe_close_osint_by_coverage,
            upsert_osint_hit_from_tool,
        )

        if is_search_country_wise_tool(tool_name) or tool_name.endswith("list_es_indices") or tool_name.endswith(
            "es_cluster_health"
        ):
            # 未满足 4.1+4.2 时：不点亮 4.3、不 kickoff、不落命中（防步骤3未完就跑社工库）
            # 但禁止裸 return：Agent 连打 ES 时若跳过引擎，4.2 会永远停在 running
            if not can_advance_to_osint(task_id).get("ok"):
                try:
                    from report_04.step_reconcile import ensure_osint_not_premature

                    ensure_osint_not_premature(store, task_id)
                except Exception:
                    pass
                try:
                    from report_04.engine import run_post_tool_light

                    run_post_tool_light(store, task_id)
                except Exception as exc:
                    logger.warning(
                        "社工库抢跑后仍推进引擎失败 task=%s: %s", task_id, exc
                    )
                return
            kickoff_osint_if_ready(store, task_id)
            if get_step_status(task_id, "step6_osint_es") == "pending":
                store.set_step_status(
                    task_id, "step6_osint_es", "running", message=f"社工库核验中 ({tool_name})"
                )
            if status == "success" and is_search_country_wise_tool(tool_name):
                upsert_osint_hit_from_tool(
                    task_id,
                    tool_args=tool_args,
                    tool_output=tool_output,
                    tool_output_id=tool_output_id,
                )
                maybe_close_osint_by_coverage(store, task_id, reason="工具后覆盖度")
            # 4.3 合法路径也走引擎：预建发文子节点 / 纠正抢跑，勿裸 return 跳过
            try:
                from report_04.engine import run_post_tool_light

                run_post_tool_light(store, task_id)
            except Exception as exc:
                logger.warning("社工库后引擎推进失败 task=%s: %s", task_id, exc)
            return
    except Exception as exc:
        logger.warning("社工库 post_tool 失败 task=%s tool=%s: %s", task_id, tool_name, exc)

    # OCR/Vision 快路径：无 profile/post 产物，必须先写图片流再退出。
    # GPT 并行多工具时 db_sink 易在尾部超时，导致 tool_outputs 已成功、步骤五永远 pending。
    if tool_name in _STEP5_STREAM_TOOLS:
        _prepare_enter_step5(store, task_id)
        try:
            _on_step5_tool_after(
                store,
                task_id,
                tool_name,
                tool_args,
                success=(status == "success"),
            )
            if status == "success" and is_stream_compare_ready(task_id):
                _try_complete_step5_if_settled(store, task_id)
                if get_step_status(task_id, "step5_streams") == "completed":
                    _maybe_advance_step67(store, task_id)
        except Exception as exc:
            logger.exception("step5 快路径失败 task=%s tool=%s: %s", task_id, tool_name, exc)
        return

    apify_platform = apify_platform_from_actor_tool(tool_name)
    if apify_platform:
        _LAST_APIFY_HINT[task_id] = tool_name.replace("mcp_apify_", "")

    # 方案 A：种子工具失败或空结果 → 任务硬失败，拦住后续步骤
    # Apify Actor / get_actor_run 成功时通常尚无 profile，不算失败
    if seed_collect:
        if status != "success":
            fail_msg = _seed_fail_message(tool_name, tool_output, empty=False)
            _abort_or_retry_seed(store, task_id, tool_name, fail_msg, tool_output)
            return

    if status != "success":
        if platform and (
            can_update_step4_children(task_id) or can_update_step7_children(task_id)
        ):
            _prepare_step4_collect(store, task_id)
            _sync_platform_collect_steps(
                store,
                task_id,
                platform,
                {},
                tool_name=tool_name,
                tool_ok=False,
                tool_output=tool_output,
                tool_args=tool_args if isinstance(tool_args, dict) else {},
            )
            try:
                from report_04.step_reconcile import close_collect_parent_if_ready
                from report_04.phases import PROFILE_PARENT_STEP_KEY

                close_collect_parent_if_ready(
                    store, task_id, PROFILE_PARENT_STEP_KEY, "候选主页采集已尝试完毕"
                )
            except Exception:
                pass
        return

    platform_hint = _LAST_APIFY_HINT.get(task_id, "")
    if tool_name == "mcp_apify_get_dataset_items":
        ds_id = str(tool_args.get("datasetId") or tool_args.get("dataset_id") or "").strip()
        platform_hint = resolve_apify_platform_hint(
            task_id, tool_output_id, dataset_id=ds_id or None
        )
        platform = _infer_platform(tool_name, tool_args, store.get_seed_accounts(task_id), platform_hint) or platform
        if seed_collect:
            output_step_key = "step1_seed"
            collect_step = None
            store.update_tool_output_phase(tool_output_id, "step1_seed")
        elif platform:
            ds_phase = _resolve_dataset_collect_phase(task_id, platform)
            output_step_key = ds_phase
            collect_step = ds_phase
            store.update_tool_output_phase(tool_output_id, ds_phase)

    ctx: Dict[str, Any] = {
        "task_id": task_id,
        "tool_output_id": tool_output_id,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "account_id": _extract_account_id(tool_args),
        "platform_hint": platform_hint,
    }

    result_data: Dict[str, Any] = {"profiles": [], "posts": [], "candidates": [], "platforms": []}
    try:
        result_data = dispatch(tool_name, tool_output, ctx)
    except Exception as exc:
        logger.exception("normalizer 失败 tool=%s task=%s: %s", tool_name, task_id, exc)
        if seed_collect:
            fail_msg = _seed_fail_message(tool_name, str(exc), empty=True)
            _abort_or_retry_seed(store, task_id, tool_name, fail_msg, str(exc))
            return
        if platform:
            _prepare_step4_collect(store, task_id)
            _sync_platform_collect_steps(
                store,
                task_id,
                platform,
                {},
                tool_name=tool_name,
                tool_ok=False,
                tool_output=str(exc),
                tool_args=tool_args if isinstance(tool_args, dict) else {},
            )
            try:
                from report_04.step_reconcile import close_collect_parent_if_ready
                from report_04.phases import PROFILE_PARENT_STEP_KEY

                close_collect_parent_if_ready(
                    store, task_id, PROFILE_PARENT_STEP_KEY, "候选主页采集已尝试完毕"
                )
            except Exception:
                pass
        store.reconcile_collect_child_steps(task_id)
        return

    if seed_collect:
        profs = result_data.get("profiles") or []
        if not profs:
            if apify_seed_empty_ok(tool_name):
                store.set_step_status(
                    task_id,
                    "step1_seed",
                    "running",
                    message=f"Apify 种子采集进行中 ({tool_name})",
                )
                logger.info("种子 Apify 中间步 task=%s tool=%s（等待 dataset）", task_id, tool_name)
                return
            fail_msg = _seed_fail_message(tool_name, tool_output, empty=True)
            _abort_or_retry_seed(store, task_id, tool_name, fail_msg, tool_output)
            logger.warning("种子采集失败(空结果) task=%s tool=%s", task_id, tool_name)
            return
        if tool_name == "mcp_apify_get_dataset_items":
            n_drop = len(result_data.get("posts") or [])
            result_data = _strip_apify_posts_for_seed_phase(result_data)
            if n_drop:
                logger.info("Apify 种子轮丢弃 posts=%d task=%s", n_drop, task_id)

    # 兜底：若 pre_tool 未拦住抢跑发文，丢弃 posts 并纠正步骤七
    premature = _is_premature_step7_tool(tool_name, task_id)
    if premature:
        n_drop = len(result_data.get("posts") or [])
        if n_drop:
            result_data = dict(result_data)
            result_data["posts"] = []
            logger.warning("丢弃抢跑发文 posts=%d task=%s tool=%s", n_drop, task_id, tool_name)
        try:
            from report_04.step_reconcile import ensure_step7_parent_not_premature

            ensure_step7_parent_not_premature(store, task_id)
        except Exception:
            pass

    _persist_normalized(store, task_id, result_data, platform=platform, tool_name=tool_name)

    seed_plat = _task_seed_platform(task_id)
    is_mcp_seed_tool = tool_name in TOOL_TO_SEED_PLATFORM and not tool_name.startswith("mcp_apify_")
    is_apify_seed_dataset = (
        tool_name == "mcp_apify_get_dataset_items"
        and is_apify_seed_platform(seed_plat)
        and get_step_status(task_id, "step1_seed") not in {"completed", "failed", "skipped"}
    )
    if (
        (is_mcp_seed_tool or is_apify_seed_dataset)
        and (result_data.get("profiles") or [])
        and get_step_status(task_id, "step1_seed") not in {"completed", "failed", "skipped"}
        and not _task_is_terminal(store, task_id)
        and not apify_seed_empty_ok(tool_name)
    ):
        store.mark_seed_completed(task_id, result_data.get("profiles") or [], source="tool")
        _clear_seed_fail_count(store, task_id)

    if tool_name == "mcp_maigret_collect_accounts":
        cands = result_data.get("candidates") or []
        store.set_step_status(
            task_id,
            "step2_maigret",
            "completed",
            message=f"Maigret 发现 {len(cands)} 个候选",
        )
    elif tool_name.startswith("mcp_maigret_"):
        cur2 = get_step_status(task_id, "step2_maigret")
        if cur2 == "pending":
            store.set_step_status(task_id, "step2_maigret", "running", message=f"Maigret 执行中 ({tool_name})")
        cands = result_data.get("candidates") or []
        if cands and get_step_status(task_id, "step2_maigret") not in {"completed", "skipped"}:
            store.set_step_status(
                task_id,
                "step2_maigret",
                "completed",
                message=f"Maigret 发现 {len(cands)} 个候选",
            )

    if tool_name in WEB_SEARCH_TOOLS:
        if get_step_status(task_id, "step3_web_search") not in {"completed", "skipped"}:
            if can_run_step3_web_search(task_id):
                store.set_step_status(task_id, "step3_web_search", "running", message=f"网页检索中 ({tool_name})")
            else:
                logger.info(
                    "step3 跳过 tool=%s task=%s: step2_maigret=%s",
                    tool_name,
                    task_id,
                    get_step_status(task_id, "step2_maigret"),
                )
        _save_step3_candidates_from_tool(store, task_id, tool_name, result)
        _maybe_complete_step3_after_web_tool(store, task_id, tool_name)

    for prof in result_data.get("profiles") or []:
        store.build_streams_from_profile(task_id, prof)

    if seed_collect:
        return

    if platform and (
        can_update_step4_children(task_id) or can_update_step7_children(task_id)
    ):
        _sync_platform_collect_steps(
            store,
            task_id,
            platform,
            result_data,
            tool_name=tool_name,
            tool_ok=True,
            tool_output=tool_output,
            tool_args=tool_args if isinstance(tool_args, dict) else {},
        )
    try:
        from report_04.engine import run_post_tool_light

        run_post_tool_light(store, task_id)
    except Exception as exc:
        logger.warning("engine post_tool 失败 task=%s: %s", task_id, exc)
        _try_complete_profiles(store, task_id)
    # 方案 A 收口：即便 engine 中途异常，发文已齐仍关父壳
    try:
        from report_04.step_reconcile import force_close_step7_posts_if_ready

        force_close_step7_posts_if_ready(store, task_id)
    except Exception:
        pass
    _maybe_stale_step5(store, task_id)


def _persist_normalized(
    store: TaskStore,
    task_id: str,
    data: Dict[str, Any],
    *,
    platform: Optional[str] = None,
    tool_name: str = "",
) -> None:
    seed_plat = _task_seed_platform(task_id)
    is_seed_persist = (
        get_step_status(task_id, "step1_seed") not in {"completed", "skipped", "failed"}
        and (
            (tool_name in TOOL_TO_SEED_PLATFORM and not tool_name.startswith("mcp_apify_"))
            or (
                is_apify_seed_platform(seed_plat)
                and tool_name
                in {
                    APIFY_SEED_PLATFORM_TOOLS.get(seed_plat),
                    "mcp_apify_get_actor_run",
                    "mcp_apify_get_dataset_items",
                }
            )
        )
    )
    prof_step = "step1_seed" if is_seed_persist else (
        profile_platform_step_key(platform) if platform else "step4_profiles"
    )
    post_step = post_platform_step_key(platform) if platform else "step4_profiles"
    for row in data.get("profiles") or []:
        store.save_profile_row(row, step_key=prof_step)
    for row in data.get("candidates") or []:
        row["task_id"] = task_id
        if tool_name.startswith("mcp_maigret_"):
            step_key = "step2_maigret"
            match_strategy = "maigret"
        elif tool_name in WEB_SEARCH_TOOLS:
            step_key = "step3_web_search"
            match_strategy = "web_search"
        else:
            step_key = "step3_web_search"
            match_strategy = row.get("discovery_source") or "web_search"
        row.setdefault("match_strategy", match_strategy)
        if step_key == "step3_web_search" and not can_run_step3_web_search(task_id):
            continue
        store.save_candidate_rows([row], step_key=step_key)
    posts = data.get("posts") or []
    if posts:
        # 发文数据始终入库（Agent 常提前调工具）；步骤七状态由门禁单独控制
        store.save_post_rows(posts, step_key=post_step)


def _on_post_llm_call(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """LLM 回合结束后：推进管线；若「等系统」收尾则注入硬约束并同 session 续跑。"""
    ex = _extra(payload)
    assistant = str(ex.get("assistant_response") or "").strip()
    user_message = str(ex.get("user_message") or "").strip()
    task_id = _resolve_task_id(payload, user_message=user_message)
    followup_ctx: Optional[str] = None
    if not task_id:
        return None
    store = _store()
    session_id = str(payload.get("session_id") or "").strip()
    try:
        _ensure_seed_from_dialogue(store, task_id, user_message=user_message, session_id=session_id)
        if not assistant or assistant == "(empty)":
            return None
        report = _resolve_final_report_text(assistant, session_id)
        if report and is_final_report(report):
            if get_step_status(task_id, "step6_validated") != "completed":
                _maybe_advance_step67(store, task_id)
            _complete_step11_from_report(
                store, task_id, report, session_id or payload.get("session_id")
            )
            return None
        from report_04.report_parser import looks_like_report_attempt

        if looks_like_report_attempt(assistant):
            if get_step_status(task_id, "step6_validated") != "completed":
                _maybe_advance_step67(store, task_id)
            _complete_step11_from_report(
                store, task_id, assistant, session_id or payload.get("session_id")
            )
            return None
        _try_parse_seed(store, task_id, assistant, user_message)
        _try_step3_web_candidates(store, task_id, assistant)
        # 步骤4：口头 skip / 自称收口 → 落库，避免父步永远 running
        try:
            from report_04.step_reconcile import (
                apply_explicit_step4_skips_from_text,
                fail_forward_step4_unattempted_when_siblings_done,
                looks_like_step4_closure_claim,
            )

            apply_explicit_step4_skips_from_text(store, task_id, assistant)
            if looks_like_step4_closure_claim(assistant):
                fail_forward_step4_unattempted_when_siblings_done(
                    store,
                    task_id,
                    reason="Agent声明步骤4收口且未尝试，已跳过",
                )
        except Exception as exc:
            logger.warning("步骤4口头skip落库失败 task=%s: %s", task_id, exc)
        collision_advanced = False
        try:
            from report_04.stream_steps import apply_text_conclusion_from_assistant

            if apply_text_conclusion_from_assistant(store, task_id, assistant):
                _maybe_advance_step67(store, task_id)
                collision_advanced = True
        except Exception as exc:
            logger.warning("解析文本核验结论失败 task=%s: %s", task_id, exc)
        # ① 压缩关联碰撞空窗：文本核验后 / 等待句前后，同轮连推 4.1→4.2→4.3
        try:
            from report_04.engine import (
                advance_collision_phase,
                build_post_llm_followup,
            )

            if collision_advanced or _looks_like_wait_for_system_exit(assistant):
                advance_collision_phase(store, task_id, max_rounds=4)
            else:
                # 常规回合也推一档，缩短空窗
                advance_collision_phase(store, task_id, max_rounds=2)
            followup_ctx = build_post_llm_followup(task_id)
        except Exception as exc:
            logger.warning("post_llm 关联碰撞推进失败 task=%s: %s", task_id, exc)
        try:
            from report_04.osint_es import apply_osint_conclusion_from_assistant

            apply_osint_conclusion_from_assistant(store, task_id, assistant)
        except Exception as exc:
            logger.warning("解析社工库核验结论失败 task=%s: %s", task_id, exc)
        _try_complete_profiles(store, task_id)
        try:
            from report_04.step_reconcile import (
                _reconcile_vision_from_tools,
                force_close_step7_posts_if_ready,
                maybe_fail_forward_stale_step5,
            )

            _reconcile_vision_from_tools(store, task_id)
            maybe_fail_forward_stale_step5(store, task_id, min_wait_seconds=90)
            _maybe_advance_step67(store, task_id)
            # 方案 A：post_llm 再兜底关发文父壳（防 post_tool Hook 超时未关）
            force_close_step7_posts_if_ready(store, task_id)
        except Exception as exc:
            logger.warning("post_llm vision 回放失败 task=%s: %s", task_id, exc)
        if _looks_like_wait_for_system_exit(assistant):
            try:
                from report_04.session_continue import (
                    anti_wait_followup_context,
                    maybe_continue_agent_session,
                )
                from report_04.osint_es import kickoff_osint_if_ready
                from report_04.engine import build_post_llm_followup

                _maybe_advance_step67(store, task_id)
                if get_step_status(task_id, "step6_validated") == "completed":
                    kickoff_osint_if_ready(store, task_id)
                followup_ctx = build_post_llm_followup(task_id)
                # 叠加 anti_wait 文案
                try:
                    followup_ctx = followup_ctx + "\n\n" + anti_wait_followup_context(task_id)
                except Exception:
                    pass
                maybe_continue_agent_session(
                    store, task_id, reason="post_llm_wait_exit"
                )
                logger.warning(
                    "post_llm 检测到「等待系统」收尾，已注入续跑 task=%s",
                    task_id,
                )
            except Exception as exc:
                logger.warning("post_llm 等系统续跑失败 task=%s: %s", task_id, exc)
                followup_ctx = (
                    "【写报硬约束】禁止写「等待系统」后结束会话。"
                    "请保持会话，门禁放行后立刻调发文工具，再写终稿。"
                )
        else:
            # 方案 C：倒写 5/6/4.3/7 回顾却无研判/终稿 → 强制 analysis 续跑
            try:
                from report_04.session_continue import (
                    looks_like_retrospective_without_analysis,
                    maybe_continue_agent_session,
                )
                from report_04.step_reconcile import force_close_step7_posts_if_ready
                from report_04.engine import build_post_llm_followup

                if looks_like_retrospective_without_analysis(task_id, assistant):
                    force_close_step7_posts_if_ready(store, task_id)
                    wait_hint = ""
                    try:
                        from report_04.video_report import format_report_wait_hint

                        wait_hint = format_report_wait_hint(task_id)
                    except Exception:
                        wait_hint = ""
                    if wait_hint:
                        followup_ctx = (
                            build_post_llm_followup(task_id)
                            + "\n【写报硬约束】发文已齐。禁止再复述步骤5/6/4.3/7。"
                            + wait_hint
                            + "禁止调用 vision；禁止 done。"
                        )
                    else:
                        followup_ctx = build_post_llm_followup(task_id) + (
                            "\n【写报硬约束】发文已齐。禁止再复述步骤5/6/4.3/7。"
                            "请立即并行输出步骤8/9/10分析正文，再写以「一、账号基本信息」开头的终稿。"
                            "禁止调用 vision；禁止 done。"
                        )
                    maybe_continue_agent_session(
                        store,
                        task_id,
                        reason="post_llm_retrospective_exit",
                        kind="analysis",
                    )
                    logger.warning(
                        "post_llm 检测到回顾假完成，已续跑 analysis task=%s",
                        task_id,
                    )
            except Exception as exc:
                logger.warning("post_llm 回顾假完成续跑失败 task=%s: %s", task_id, exc)
        # 无终稿时尽量带回进度看板（即便未命中等待/回顾）
        if not followup_ctx:
            try:
                from report_04.session_continue import has_final_report
                from report_04.engine import build_post_llm_followup

                if not has_final_report(task_id):
                    followup_ctx = build_post_llm_followup(task_id)
            except Exception:
                pass
        if not is_progress_only(assistant) and not _looks_like_report_meta_closing(assistant):
            store.save_assistant_output(task_id, payload.get("session_id"), assistant)
    except DbError as exc:
        logger.warning("post_llm_call 失败 task=%s: %s", task_id, exc)
    if followup_ctx:
        return {"context": followup_ctx}
    return None


def _load_assistant_output_from_state(session_id: str) -> Optional[str]:
    """从 Hermes state.db 回扫最近助手消息，返回第一条符合终稿结构的正文。"""
    if not session_id:
        return None
    db_path = hermes_home() / "state.db"
    if not db_path.is_file():
        return None
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT content FROM messages
            WHERE session_id=? AND role='assistant'
              AND content IS NOT NULL AND TRIM(content) != ''
              AND TRIM(content) != '(empty)'
            ORDER BY id DESC
            LIMIT 50
            """,
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return None
    best: Optional[str] = None
    best_len = 0
    for row in rows:
        text = str(row[0] or "").strip()
        if not is_final_report(text):
            continue
        if len(text) > best_len:
            best = text
            best_len = len(text)
    return best


def _on_session_end(payload: Dict[str, Any]) -> None:
    """会话结束：先轻量收口 + 尽快 spawn 续跑，重活外置，避免 Hook 120s 超时。"""
    task_id = _resolve_task_id(payload)
    if not task_id:
        return
    session_id = str(payload.get("session_id") or "").strip() or None
    store = _store()
    fail_reason: Optional[str] = None

    # 1) 轻量：图片 fail-forward；若 5 完 6 未完则跑 validated
    try:
        from report_04.step_reconcile import _fail_forward_step5_pending_images

        _fail_forward_step5_pending_images(
            store, task_id, reason="会话结束兜底：未完成 vision"
        )
        if (
            get_step_status(task_id, "step5_streams") == "completed"
            and get_step_status(task_id, "step6_validated") != "completed"
        ):
            store.run_validated_accounts(task_id)
    except Exception as exc:
        logger.warning("on_session_end step5 轻量收口失败 task=%s: %s", task_id, exc)

    s43 = get_step_status(task_id, "step6_osint_es")
    osint_pending = s43 not in {"completed", "skipped", "failed"}

    # 2) 尽快 spawn 续跑：4.3 未终态交给 osint_worker→osint_done，避免 hold/posts 抢 inflight
    deferred = False
    try:
        from report_04.session_continue import (
            has_final_report,
            maybe_continue_agent_session,
            should_defer_finalize,
        )

        if not has_final_report(task_id):
            if osint_pending:
                deferred = True
            else:
                try:
                    from report_04.step_reconcile import skip_exhausted_step7_post_mcp

                    skip_exhausted_step7_post_mcp(store, task_id, session_end=True)
                except Exception as exc:
                    logger.warning("session_end skip MCP 发文失败 task=%s: %s", task_id, exc)
                maybe_continue_agent_session(store, task_id, reason="session_end")
                deferred = should_defer_finalize(task_id)
    except Exception as exc:
        logger.warning("on_session_end 续跑失败 task=%s: %s", task_id, exc)

    # 3) 社工库：未终态则外置子进程；已终态只做轻量 close（通常 no-op）
    try:
        from report_04.osint_es import close_osint_on_session_end, spawn_detached_osint

        if osint_pending:
            if not spawn_detached_osint(task_id):
                # spawn 失败才同步兜底（可能拖慢 Hook，但优于永不跑 4.3）
                from report_04.osint_es import kickoff_osint_if_ready

                kickoff_osint_if_ready(store, task_id)
                close_osint_on_session_end(store, task_id)
                try:
                    from report_04.session_continue import (
                        has_final_report,
                        maybe_continue_agent_session,
                        should_defer_finalize,
                    )

                    if not has_final_report(task_id):
                        maybe_continue_agent_session(
                            store, task_id, reason="session_end"
                        )
                        deferred = should_defer_finalize(task_id)
                except Exception:
                    pass
            else:
                deferred = True
        else:
            close_osint_on_session_end(store, task_id)
    except Exception as exc2:
        logger.warning("on_session_end 社工库收口失败 task=%s: %s", task_id, exc2)

    # 4) 终稿解析保持简短（禁止同步 urlopen 读完整 stream）
    try:
        user_message = str(_extra(payload).get("user_message") or "")
        _ensure_seed_from_dialogue(store, task_id, user_message=user_message, session_id=session_id or "")
        assistant = _resolve_final_report_text("", session_id or "")
        if not assistant:
            assistant = _load_assistant_output_from_state(session_id or "")
        if assistant and is_final_report(assistant):
            if get_step_status(task_id, "step6_validated") != "completed":
                _maybe_advance_step67(store, task_id)
            _complete_step11_from_report(store, task_id, assistant, session_id)
            deferred = False
        elif assistant:
            from report_04.report_parser import looks_like_report_attempt

            if looks_like_report_attempt(assistant):
                if get_step_status(task_id, "step6_validated") != "completed":
                    _maybe_advance_step67(store, task_id)
                _complete_step11_from_report(store, task_id, assistant, session_id)
                deferred = False
            else:
                _try_parse_seed(store, task_id, assistant, user_message)
                _try_step3_web_candidates(store, task_id, assistant)
    except Exception as exc:
        logger.warning("on_session_end 兜底失败 task=%s: %s", task_id, exc)

    # 5) deferred 则保持 running 并跳过重 finalize
    try:
        if not fail_reason and not deferred:
            fail_reason = _early_exit_before_posts_message(task_id)
        from report_04.orchestrator import run_full_reconcile_if_requested

        run_full_reconcile_if_requested(store, task_id)
        if deferred:
            try:
                from collect_01 import db as _db

                _db.execute(
                    """
                    UPDATE hermes_tasks
                    SET status='running',
                        finished_at=NULL,
                        error_message=NULL,
                        updated_at=NOW(3)
                    WHERE task_id=%s AND status NOT IN ('failed', 'completed', 'cancelled')
                    """,
                    (task_id,),
                )
                logger.info(
                    "on_session_end 暂缓 finalize：已续跑/社工库外置，保持 running task=%s",
                    task_id,
                )
            except Exception as exc2:
                logger.warning("暂缓 finalize 失败 task=%s: %s", task_id, exc2)
        else:
            store.finalize_task(task_id, session_ended=True, fail_reason=fail_reason)
    except Exception as exc:
        logger.warning("on_session_end finalize 失败 task=%s: %s", task_id, exc)
        try:
            from collect_01 import db as _db

            msg = (fail_reason or "会话结束收口失败（Hook 异常）")[:500]
            _db.execute(
                """
                UPDATE hermes_tasks
                SET status='failed',
                    error_message=%s,
                    finished_at=COALESCE(finished_at, NOW(3)),
                    updated_at=NOW(3)
                WHERE task_id=%s AND status NOT IN ('failed', 'completed', 'cancelled')
                """,
                (msg, task_id),
            )
        except Exception as exc2:
            logger.warning("on_session_end 兜底标 failed 失败 task=%s: %s", task_id, exc2)


def _extract_account_id(tool_args: Dict[str, Any]) -> Optional[str]:
    for key in ("user_id", "username", "screen_name", "channelId", "channel_id", "uid"):
        val = tool_args.get(key)
        if val:
            return str(val)
    return None
