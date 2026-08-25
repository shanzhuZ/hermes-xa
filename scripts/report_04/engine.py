"""04 写报编排引擎 v2：确定性推进步骤，减轻 Agent 空转与 Hook 超时。

原则：
- 步骤终态仅由 Hook/task_store 写入；Java 粗同步对 account_report 不写库。
- post_tool 热路径只跑轻量收口，全量 reconcile 仅 session_end + 环境变量。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from collect_01 import db
from report_04.gates import (
    can_advance_to_step5,
    can_advance_to_step7,
    get_step_status,
    is_stream_compare_ready,
    step4_profiles_terminal,
)
from report_04.orchestrator import infer_gate_step, block_tool_reason
from report_04.phases import (
    ANALYSIS_STEP_KEYS,
    PHASE_DONE,
    POST_PARENT_STEP_KEY,
    PROFILE_PARENT_STEP_KEY,
    STEP5_STREAM_TOOLS,
)

logger = logging.getLogger(__name__)

ENGINE_VERSION = "v2"

# 步骤四：距上次成功主页工具超过该秒数且无抢跑干扰时，fail-forward 跳过未尝试子节点
STEP4_QUIET_SKIP_SECONDS = 60.0


def snapshot(task_id: str) -> Dict[str, Any]:
    """给 pre_llm / 日志用的当前编排快照。"""
    gate = infer_gate_step(task_id)
    return {
        "engineVersion": ENGINE_VERSION,
        "gateStep": gate,
        "step4Terminal": step4_profiles_terminal(task_id),
        "step5": get_step_status(task_id, "step5_streams"),
        "step6": get_step_status(task_id, "step6_validated"),
        "step7": get_step_status(task_id, "step7_posts"),
        "step11": get_step_status(task_id, "step11_report"),
    }


def format_system_progress_board(task_id: str) -> str:
    """每轮注入：进度 + 下一步。早期步骤禁止催「完成步骤5」（会诱导跳步写核验）。"""
    gate = infer_gate_step(task_id)
    s1 = get_step_status(task_id, "step1_seed") or "pending"
    s2 = get_step_status(task_id, "step2_maigret") or "pending"
    s3 = get_step_status(task_id, "step3_web_search") or "pending"
    s4 = get_step_status(task_id, "step4_profiles") or "pending"
    s5 = get_step_status(task_id, "step5_streams") or "pending"
    s6 = get_step_status(task_id, "step6_validated") or "pending"
    s43 = get_step_status(task_id, "step6_osint_es") or "pending"
    s7 = get_step_status(task_id, "step7_posts") or "pending"

    # 尚未进入四、关联碰撞：只展示前四步，严禁催核验/发文
    early_gates = {
        "step1_seed",
        "step2_maigret",
        "step3_web_search",
        "step4_profiles",
    }
    if gate in early_gates and s5 not in {"running", "completed", "skipped"}:
        lines = [
            "【系统进度·采集前半段】",
            f"- 步骤1 种子 step1_seed: {s1}",
            f"- 步骤2 Maigret step2_maigret: {s2}",
            f"- 步骤3 网页检索 step3_web_search: {s3}",
            f"- 步骤4 主页 step4_profiles: {s4}",
            f"【当前编排】{gate}",
        ]
        early_next = {
            "step1_seed": (
                "【下一步】先采种子主页（Twitter→mcp_twitter_get_user_info(screen_name=…) 等）；"
                "禁止跳写步骤5 [文本核验结论]；禁止宣称不启动步骤6；禁止发文。"
            ),
            "step2_maigret": (
                "【下一步】只调 mcp_maigret_collect_accounts；"
                "禁止跳写步骤5核验/步骤6/发文。"
            ),
            "step3_web_search": (
                "【下一步】做网页线索检索并落候选；禁止跳写步骤5核验/发文。"
            ),
            "step4_profiles": (
                "【下一步】对每个候选平台调主页工具；全部终态后系统才启动 4.1；"
                "禁止提前输出 [文本核验结论]；禁止发文。"
            ),
        }
        lines.append(early_next.get(gate, "【下一步】按当前编排继续；禁止跳步。"))
        return "\n".join(lines)

    lines = [
        "【系统进度·四、关联碰撞→五、内容采集】",
        f"- 4.1 信息核验 step5_streams: {s5}",
        f"- 4.2 可信收敛 step6_validated: {s6}",
        f"- 4.3 社工库 step6_osint_es: {s43}",
        f"- 步骤7 发文 step7_posts: {s7}",
        f"【当前编排】{gate}",
    ]
    try:
        from report_04.gates import can_run_step7_collect, posts_substantively_ready
        from report_04.step_reconcile import list_unattempted_post_platforms

        if posts_substantively_ready(task_id) or s7 in {"completed", "skipped"}:
            try:
                from report_04.gates import can_advance_to_analysis
                from report_04.video_report import format_report_wait_hint

                if not can_advance_to_analysis(task_id).get("ok"):
                    lines.append(
                        "【下一步】发文已齐，内容采集父壳 step7_posts 未终态（常为等视频）→ 保持会话；"
                        "禁止提前写步骤8/9/10与终稿；禁止同步 mcp_video2frame_*。"
                    )
                else:
                    wait_hint = format_report_wait_hint(task_id)
                    if wait_hint:
                        lines.append(wait_hint)
                    else:
                        lines.append(
                            "【下一步】内容采集父壳已终态 → 立刻写步骤8/9/10，再写「一、账号基本信息」终稿；"
                            "禁止结束会话；禁止回写步骤5/6/4.3；禁止 vision。"
                        )
            except Exception:
                lines.append(
                    "【下一步】内容采集父壳已终态 → 立刻写步骤8/9/10，再写「一、账号基本信息」终稿；"
                    "禁止结束会话；禁止回写步骤5/6/4.3；禁止 vision。"
                )
        elif can_run_step7_collect(task_id):
            todo = list_unattempted_post_platforms(task_id) or []
            if todo:
                hints = "; ".join(
                    f"{x.get('platform')}:{x.get('tool_hint')}" for x in todo[:6]
                )
                lines.append(
                    f"【下一步】发文已开放 → 本回合必须调发文工具（{len(todo)} 个）：{hints}；"
                    "禁止只写等待句后结束会话。"
                )
            else:
                lines.append(
                    "【下一步】发文门禁已开且待采列表为空 → 保持会话或写步骤8～11；禁止 done。"
                )
        elif s5 not in {"completed", "skipped"}:
            lines.append(
                "【下一步】在步骤4已终态前提下输出 [文本核验结论]（描述证据即可）；"
                "4.2 validated 由系统自动跑（至少含种子），禁止写「不启动步骤6/不宜前推」；"
                "禁止发文；禁止写「等待」后结束会话。"
            )
        else:
            lines.append(
                "【下一步】4.2/4.3 由系统自动推进（Agent 勿拒绝、勿空等 done）；"
                "门禁一开必须立刻调步骤7发文工具。"
            )
    except Exception:
        lines.append("【下一步】禁止结束会话；按当前编排继续。")
    return "\n".join(lines)


def build_agent_context(task_id: str) -> Optional[str]:
    """注入 Agent：系统进度看板 + 当前 gate + 应做之事。"""
    gate = infer_gate_step(task_id)
    lines: List[str] = [
        format_system_progress_board(task_id),
        f"【04引擎 {ENGINE_VERSION}】当前编排步骤：{gate}",
    ]

    if gate == "step1_seed":
        lines.append(
            "步骤1：必须先调用种子平台主页工具落库；"
            "禁止跳写步骤5 [文本核验结论]、禁止 session_search、禁止发文。"
        )
    elif gate == "step2_maigret":
        lines.append(
            "步骤2：只调 mcp_maigret_collect_accounts(username=种子handle)；"
            "禁止跳写步骤5核验。"
        )
    elif gate == "step3_web_search":
        lines.append(
            "步骤3：网页线索检索；禁止跳写步骤5核验/发文。"
        )
    elif gate == "step4_profiles":
        lines.append(
            "步骤3（账号主页采集）：须对线索发现（步骤2 Maigret + 步骤3 网页）产出的"
            "每个可采集候选平台调用主页工具；禁止因「与种子不相似」跳过；"
            "相似度只在步骤6认定。禁止社工库/发文/vision；采不到或失败再 skip。"
        )
        open_rows = db.fetch_all(
            """
            SELECT step_key, status FROM collect_phase_steps
            WHERE task_id=%s AND parent_step_key=%s
              AND status IN ('pending', 'running')
            ORDER BY step_order, step_key
            """,
            (task_id, PROFILE_PARENT_STEP_KEY),
        )
        if open_rows:
            from report_04.phases import APIFY_TOOL_PLATFORM, TOOL_PLATFORM

            lines.append(f"【本回合必须处理】未终态主页子步 {len(open_rows)} 个：")
            rev_apify = {p: t for t, p in APIFY_TOOL_PLATFORM.items()}
            rev_mcp = {}
            for t, p in TOOL_PLATFORM.items():
                if p not in rev_apify and "get_user_tweets" not in t and "feeds" not in t and "analyze_channel" not in t:
                    rev_mcp.setdefault(p, t)
            for r in open_rows[:12]:
                sk = str(r.get("step_key") or "")
                plat = sk.replace("step4_profile_", "", 1) if sk.startswith("step4_profile_") else sk
                hint = rev_mcp.get(plat) or rev_apify.get(plat) or f"Apify/MCP 主页工具({plat})"
                if plat in rev_apify:
                    hint = f"{rev_apify[plat]} → get_actor_run → get_dataset_items"
                lines.append(f"- {sk}: {hint}")
        lines.append(
            "全部子步终态后系统自动跑 4.1→4.2→4.3；未完成步骤3前调用社工库会被拦截。"
        )

    elif gate == "step5_streams":
        from report_04.sink import _pending_image_stream_lines  # noqa: PLC0415 — 复用既有 URL 列表

        pending = _pending_image_stream_lines(task_id)
        s5 = get_step_status(task_id, "step5_streams")
        if s5 in {"completed", "skipped"}:
            lines.append(
                "步骤5已系统收口，禁止再 vision/OCR；"
                "禁止结束会话、禁止写「等待系统/会话保持」。"
                "系统推进 4.2/4.3 后，本会话必须立刻调发文工具。"
            )
        elif not step4_profiles_terminal(task_id):
            pend = None
            try:
                from report_04.gates import _step4_profile_children_pending

                pend = _step4_profile_children_pending(task_id)
            except Exception:
                pend = "step4_profile_*"
            lines.append(
                f"步骤4未全终态（仍有 {pend or 'pending/running'}），请先完成/跳过该平台主页，再并行 vision。"
            )
        elif pending:
            lines.append(
                f"步骤5：请本回合并行 {len(pending)} 次 vision_analyze（一次齐发），image_url 必须用："
            )
            lines.extend(pending[:8])
        else:
            lines.append(
                "步骤5：图片流已齐或由系统管线处理；请输出 [文本核验结论]（若尚未输出）。"
                "结论只陈述证据；禁止写「暂不启动步骤6/不宜纳入 validated」——"
                "4.2 由系统自动收敛（种子必进 validated）。"
                "禁止写「等待系统/会话保持中」并 done；保持会话，门禁放行后立刻调发文。"
            )

    elif gate == "step6_validated":
        lines.append(
            "步骤6（4.2）：系统正在/即将收敛可信账号（非 Agent 决定是否启动）；"
            "禁止输出「等待系统/不启动步骤6」后 done；"
            "系统收口后同一会话必须立刻进入发文。"
        )

    elif gate == "step6_osint_es":
        from report_04.osint_es import format_pending_urls_for_agent

        pending = format_pending_urls_for_agent(task_id)
        lines.append(
            "步骤4.3 社工库核验：主路径由系统自动查 ES；"
            "若下列仍有待查 URL，可补调 mcp_es_search_search_country_wise"
            "（query_text=profile_url，按需 field；可选 person_name）；"
            "禁止 search_facebook/search_worldpeople；禁止发文工具。"
            "禁止结束会话空等。"
        )
        if pending:
            lines.append(f"待查 URL（{len(pending)}）：")
            lines.extend(pending[:20])
        else:
            lines.append(
                "待查 URL 已由系统查完或无可查 URL；"
                "可选输出 [社工库核验结论]，然后同一会话立刻进入步骤7调发文工具（禁止 done）。"
            )
        # 4.3 已终态：点名步骤7待采，逼 Agent 立刻调发文工具
        if get_step_status(task_id, "step6_osint_es") in {"completed", "skipped"}:
            try:
                from report_04.step_reconcile import list_unattempted_post_platforms

                todo7 = list_unattempted_post_platforms(task_id)
                if todo7:
                    lines.append(
                        "【下一步硬强制】步骤7须先对下列 validated 调用发文工具"
                        f"（{len(todo7)} 个尚未尝试），禁止直接写分析/终稿："
                    )
                    for item in todo7[:10]:
                        lines.append(
                            f"- {item.get('platform')}: {item.get('tool_hint')}"
                        )
            except Exception:
                pass

    elif gate == "step7_posts":
        # 步骤7 发文（勿称「步骤5/UI步骤5」）
        s7 = get_step_status(task_id, "step7_posts")
        open_posts = db.fetch_all(
            """
            SELECT step_key, status FROM collect_phase_steps
            WHERE task_id=%s AND parent_step_key=%s
              AND status IN ('pending', 'running')
            ORDER BY step_order, step_key
            """,
            (task_id, POST_PARENT_STEP_KEY),
        )
        open_keys = [str(r.get("step_key") or "") for r in (open_posts or [])]
        if s7 in {"pending", None, ""}:
            lines.append(
                "【步骤7发文·待启动】4.3 已终态，发文父节点等待你调用工具后才会 running。"
                "本回合必须对下列 validated 发起发文工具；"
                "禁止只写步骤5核验/等待系统、禁止进分析/终稿、禁止结束会话。"
            )
        else:
            lines.append(
                "【步骤7发文·硬强制】本回合继续对各平台调用发文工具。"
                "禁止输出分析/终稿；允许工具已调用但失败或 0 条再 skip。主页≠发文。"
            )
        try:
            from report_04.step_reconcile import list_unattempted_post_platforms

            todo = list_unattempted_post_platforms(task_id)
            if todo:
                lines.append(
                    f"【必须立即调用·禁止空过】共 {len(todo)} 个平台，请本回合并行发起工具："
                )
                for item in todo[:12]:
                    lines.append(
                        f"- {item.get('platform')}: {item.get('tool_hint')}"
                    )
            else:
                wait_hint = ""
                try:
                    from report_04.video_report import format_report_wait_hint

                    wait_hint = format_report_wait_hint(task_id)
                except Exception:
                    wait_hint = ""
                if wait_hint:
                    lines.append(
                        "发文工具均已尝试。" + wait_hint
                        + "禁止再调 vision；禁止回写步骤5/6/4.3。"
                    )
                else:
                    lines.append(
                        "发文工具均已尝试；系统将关 step7 父壳。"
                        "请立刻写步骤8/9/10 分析正文，再写以「一、账号基本信息」开头的终稿。"
                        "禁止再调 vision；禁止回写步骤5/6/4.3。"
                    )
        except Exception:
            pass
        if open_keys:
            lines.append(
                "仍有未完成发文子步："
                + ", ".join(open_keys[:12])
                + "。必须继续采集，禁止结束会话空等。"
            )
        lines.append(
            "发文入库后若有可下载视频，Hook 会挂 step7_video_* 后台分析："
            "不挡发文子步 completed，但挡住 step7_posts 父壳收口；"
            "须等父壳终态后再写步骤8/9/10与终稿；禁止同步 mcp_video2frame_*。"
        )

    elif gate in ANALYSIS_STEP_KEYS or gate == "step11_report":
        try:
            from report_04.step_reconcile import list_unattempted_post_platforms

            leftover = list_unattempted_post_platforms(task_id)
            if leftover:
                lines.append(
                    "【违规风险】仍有 validated 平台未尝试发文工具，禁止写步骤8～11；"
                    "请立刻回调发文工具："
                )
                for item in leftover[:8]:
                    lines.append(
                        f"- {item.get('platform')}: {item.get('tool_hint')}"
                    )
        except Exception:
            pass
        lines.append(
            f"当前 task_id={task_id}。图片资产由系统 Hook 兜底；"
            "禁止在终稿前缀/正文写「跳过步骤7.5 / 管线未找到 / 即席执行」等元叙述。"
        )
        try:
            from report_04.image_assets import has_stored_images

            if not has_stored_images(task_id):
                lines.append(
                    "若需补跑图片入库（勿写入报告正文）："
                    f"python -m image_pipeline.run --task-id {task_id} --force-analyze ；"
                    "失败只记日志，勿判失败，继续步骤8/9/10。"
                )
            else:
                lines.append(
                    "图片资产已入库，步骤8可优先结合 collect_images / "
                    f"GET /api/tasks/{task_id}/images 写分析，勿重复全量空跑 vision。"
                )
        except Exception:
            lines.append(
                "步骤8前可确认图片管线："
                f"python -m image_pipeline.run --task-id {task_id} --force-analyze"
            )
        wait_hint = ""
        try:
            from report_04.video_report import format_report_wait_hint

            wait_hint = format_report_wait_hint(task_id)
        except Exception:
            wait_hint = ""
        if wait_hint:
            lines.append(wait_hint)
            if gate in ANALYSIS_STEP_KEYS:
                lines.append("步骤8/9/10：同一次响应内并行输出三步分析正文，但禁止终稿。")
        elif gate == "step11_report":
            lines.append(
                "步骤11：终稿必须以「一、账号基本信息」开头，勿在第一节前写进度/管线句。"
                "第三章每条观点「发文作证」库内有帖则写 3～5 条（不够则写尽并注明仅见 N 条，禁止编造）。"
                "第五章至少五「是」、每段约 80～120 字；第六章研判不少于约 400 字且后续核查不少于 4 步。"
                "终稿严格按 Skill/collect-rules 骨架输出，最末尾必须保留 [报告标签]标签1,标签2[/报告标签] 成对块（按全文归纳替换示例标签，禁止删块）。"
                "Twitter/X：写「点赞他人推文 N 次」「被列入清单 N 次」，禁止写「获赞」，禁止在正文出现英文字段名。"
            )
            try:
                from report_04.osint_es import format_osint_hits_for_report

                osint_sum = format_osint_hits_for_report(task_id)
                if osint_sum:
                    lines.append(osint_sum)
                else:
                    lines.append("社工库无命中或未查：终稿可不写或一句「社工库未命中」。")
            except Exception:
                pass
        elif gate in ANALYSIS_STEP_KEYS:
            lines.append("步骤8/9/10：同一次响应内并行输出三步分析正文。")

    return "\n".join(lines)


def advance_collision_phase(store: Any, task_id: str, *, max_rounds: int = 4) -> None:
    """压缩「四、关联碰撞」空窗：同轮尽量连推 4.1→4.2→4.3。"""
    for _ in range(max(1, int(max_rounds))):
        before = (
            get_step_status(task_id, "step5_streams"),
            get_step_status(task_id, "step6_validated"),
            get_step_status(task_id, "step6_osint_es"),
        )
        try:
            _auto_step5_step6(store, task_id)
        except Exception as exc:
            logger.warning("advance_collision 失败 task=%s: %s", task_id, exc)
            break
        after = (
            get_step_status(task_id, "step5_streams"),
            get_step_status(task_id, "step6_validated"),
            get_step_status(task_id, "step6_osint_es"),
        )
        if after == before:
            break
        if after[2] in {"completed", "skipped"}:
            break


def build_post_llm_followup(task_id: str) -> str:
    """post_llm 回注：进度看板 + 下一步动作 + 禁止 done。"""
    from report_04.session_continue import build_next_action_hint

    board = format_system_progress_board(task_id)
    hint = build_next_action_hint(task_id)
    tail = (
        "\n【写报硬约束】禁止以「等待系统/会话保持/等步骤6/4.3」结束本轮。"
        "无工具可调时保持会话；门禁放行后立刻调发文或写分析/终稿。"
    )
    if hint:
        return board + "\n" + hint + tail
    return board + tail


def enrich_block_reason(task_id: str, reason: str) -> str:
    ctx = build_agent_context(task_id)
    if not ctx:
        return reason
    return reason + "\n\n" + ctx


def run_post_tool_light(store: Any, task_id: str) -> None:
    """post_tool 末尾：毫秒～百毫秒级，禁止 reconcile_stuck_pipeline。"""
    from report_04.step_reconcile import (
        close_collect_parent_if_ready,
        ensure_osint_not_premature,
        ensure_step4_parent_not_premature,
        ensure_step5_not_premature,
        ensure_step6_not_premature,
        ensure_step7_awaits_agent_tool,
        ensure_step7_parent_active,
        ensure_step7_parent_not_premature,
        maybe_close_abandoned_step4,
    )

    try:
        ensure_step4_parent_not_premature(store, task_id)
        ensure_step5_not_premature(store, task_id)
        ensure_step6_not_premature(store, task_id)
        ensure_osint_not_premature(store, task_id)
        ensure_step7_parent_not_premature(store, task_id)
        # 晚到发文子节点时回开已 completed 的 step7_posts（及 phase_content 壳）
        ensure_step7_parent_active(store, task_id)
        # 违规空过的发文子步回开为 pending（父节点不强制 running）
        from report_04.step_reconcile import reopen_unattempted_empty_skipped_posts

        reopen_unattempted_empty_skipped_posts(store, task_id)
        # 无发文工具却 running：降回 pending，禁止抢跑甩开 stream
        ensure_step7_awaits_agent_tool(store, task_id)
    except Exception as exc:
        logger.warning("engine ensure parent 失败 task=%s: %s", task_id, exc)

    # 步骤3：有进展超时才 fail-forward；抢跑 4.3 时不 skip（由 pre_tool 拉回）
    try:
        maybe_close_abandoned_step4(
            store, task_id, min_quiet_seconds=STEP4_QUIET_SKIP_SECONDS, force=False
        )
    except Exception as exc:
        logger.warning("engine step4 收口失败 task=%s: %s", task_id, exc)

    try:
        close_collect_parent_if_ready(
            store, task_id, PROFILE_PARENT_STEP_KEY, "候选主页采集已尝试完毕"
        )
        close_collect_parent_if_ready(
            store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕"
        )
    except Exception as exc:
        logger.warning("engine close parent 失败 task=%s: %s", task_id, exc)

    _auto_step5_step6(store, task_id)

    try:
        from report_04.orchestrator import on_post_tool_step7_close_parent

        on_post_tool_step7_close_parent(store, task_id)
    except Exception as exc:
        logger.warning("engine step7 close 失败 task=%s: %s", task_id, exc)


def run_pre_llm_auto(store: Any, task_id: str) -> None:
    """每轮 LLM 前：推进可自动完成的步骤（关联碰撞尽量连推）。"""
    run_post_tool_light(store, task_id)
    advance_collision_phase(store, task_id, max_rounds=3)
    _try_finalize_report(store, task_id, light_only=True)


def run_session_finalize_light(
    store: Any,
    task_id: str,
    *,
    reason: str = "会话结束：Agent stream 已结束",
) -> None:
    """session_end：与 stream 一并结束流程（收口未终态步骤，禁止新开分析 running）。"""
    from report_04.task_store import _reconcile_report_post_child_steps
    from report_04.step_reconcile import (
        close_collect_parent_if_ready,
        reconcile_step4_and_step7_children,
        reconcile_step7_from_post_tools,
    )
    from report_04.orchestrator import close_open_steps_for_session_end

    _auto_step5_step6(store, task_id)
    _reconcile_report_post_child_steps(store, task_id)
    try:
        reconcile_step7_from_post_tools(store, task_id)
        reconcile_step4_and_step7_children(store, task_id)
    except Exception as exc:
        logger.warning("engine session reconcile children 失败 task=%s: %s", task_id, exc)

    store.reconcile_collect_child_steps(task_id)
    close_collect_parent_if_ready(store, task_id, POST_PARENT_STEP_KEY, "发文采集已尝试完毕")
    # stream 已结束：未终态步骤全部收口，不再 advance_to_analysis（否则会空挂 running）
    close_open_steps_for_session_end(store, task_id, reason=reason)
    if get_step_status(task_id, "step7_posts") in {"completed", "skipped"}:
        try:
            from report_04.image_assets import spawn_second_image_pipeline

            spawn_second_image_pipeline(task_id)
        except Exception as exc:
            logger.warning("session_finalize 后台第二次图片管线失败 task=%s: %s", task_id, exc)


def _prepare_step7_after_osint(store: Any, task_id: str) -> None:
    """4.3 终态后：仅预建发文子节点，父节点保持 pending。

    禁止在此把 step7_posts / phase_content 标成 running——须等 Agent 真正调用发文工具
   （sink.start_step7_if_ready），避免 stream 还在 4.1 叙述时树上发文已抢跑。
    """
    from report_04.gates import can_run_step7_collect

    if not can_run_step7_collect(task_id):
        return
    try:
        store.prepare_step7_children_pending(task_id)
    except Exception as exc:
        logger.warning("engine prepare_step7 失败 task=%s: %s", task_id, exc)
    # 纠正误抢跑：running 但无任何发文工具/子步 running → 降回 pending
    try:
        from report_04.step_reconcile import ensure_step7_awaits_agent_tool

        ensure_step7_awaits_agent_tool(store, task_id)
    except Exception as exc:
        logger.warning("engine ensure_step7_awaits_agent 失败 task=%s: %s", task_id, exc)


def _auto_step5_step6(store: Any, task_id: str) -> None:
    """系统自动推进，但每轮最多推一档，避免甩开 Agent/stream。

    档位：4.1 kickoff/settle → 4.2 validated → 4.3 osint → 预建发文子节点（不点亮 running）。
    """
    if not can_advance_to_step5(task_id).get("ok"):
        return

    s5 = get_step_status(task_id, "step5_streams")
    if s5 not in {"completed", "skipped"}:
        if step4_profiles_terminal(task_id):
            store.kickoff_step5_if_ready(task_id)
        if get_step_status(task_id, "step5_streams") == "running" and is_stream_compare_ready(
            task_id
        ):
            try:
                from report_04.sink import _try_complete_step5_if_settled

                _try_complete_step5_if_settled(store, task_id)
            except Exception as exc:
                logger.warning("engine step5 settle 失败 task=%s: %s", task_id, exc)
        return  # 本轮只推 4.1

    if get_step_status(task_id, "step6_validated") != "completed":
        try:
            store.run_validated_accounts(task_id)
            logger.info("engine 自动步骤6 task=%s", task_id)
        except Exception as exc:
            logger.warning("engine run_validated 失败 task=%s: %s", task_id, exc)
        # 4.2 仍未终态：本轮停；若刚完成则继续推 4.3（系统活，不点亮发文）
        if get_step_status(task_id, "step6_validated") != "completed":
            return

    # 4.2 已完成：推 4.3；终态后只 prepare 发文子节点
    try:
        from report_04.osint_es import kickoff_osint_if_ready, maybe_fail_forward_stale_osint

        kickoff_osint_if_ready(store, task_id)
        maybe_fail_forward_stale_osint(store, task_id, min_wait_seconds=120.0)
    except Exception as exc:
        logger.warning("engine kickoff/fail-forward osint 失败 task=%s: %s", task_id, exc)
    _prepare_step7_after_osint(store, task_id)


def _try_finalize_report(store: Any, task_id: str, *, light_only: bool) -> None:
    """终稿已 completed 时把任务标 completed（并可选轻量 finalize）。"""
    if get_step_status(task_id, "step11_report") != "completed":
        return
    task = store.get_task(task_id) or {}
    if str(task.get("status") or "") in {"completed", "failed"}:
        return
    has_summary = db.fetch_one(
        "SELECT id FROM hermes_user_dialogues WHERE task_id=%s AND msg_type='summary' LIMIT 1",
        (task_id,),
    )
    if not has_summary:
        return
    if light_only:
        db.execute(
            """
            UPDATE hermes_tasks
            SET status='completed', current_phase=%s,
                finished_at=COALESCE(finished_at, NOW(3)), updated_at=NOW(3)
            WHERE task_id=%s AND status NOT IN ('failed', 'cancelled')
            """,
            (PHASE_DONE, task_id),
        )
        logger.info("engine 终稿已出，任务标 completed task=%s", task_id)
        return
    store.finalize_task(task_id)


def pre_tool_allowed(task_id: str, tool_name: str, *, phase: Optional[str] = None) -> Optional[str]:
    """统一 pre_tool：返回 block reason（含引擎指引）。"""
    if not tool_name:
        return None
    # sink 里还有更细的门禁，此处只做编排白名单
    reason = block_tool_reason(task_id, tool_name, phase=phase)
    if reason:
        return enrich_block_reason(task_id, reason)
    if tool_name in STEP5_STREAM_TOOLS and get_step_status(task_id, "step5_streams") in {
        "completed",
        "skipped",
    }:
        gate = infer_gate_step(task_id)
        if gate == "step7_posts":
            tip = "当前为步骤7发文：禁止 vision；请调发文工具或写步骤8～11。"
        elif gate in ANALYSIS_STEP_KEYS or gate == "step11_report":
            tip = "当前为研判/写报阶段：禁止 vision；请直接写步骤8/9/10；发文与视频齐后再写终稿。"
        elif gate in {"step6_validated", "step6_osint_es"}:
            tip = "当前为步骤6/4.3：禁止 vision；保持会话，门禁放行后立刻调发文工具。"
        else:
            tip = "步骤5已收口，禁止再 vision/OCR；请按当前编排步骤继续。"
        return enrich_block_reason(task_id, f"步骤5图片流已收口，禁止再调用 vision/OCR。{tip}")
    return None


def full_reconcile_enabled() -> bool:
    return os.environ.get("HERMES_REPORT_FULL_RECONCILE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
