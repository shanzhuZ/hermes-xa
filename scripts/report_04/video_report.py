"""04 视频门禁 / 超时强杀 / 终稿并入「视频观察」。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List

from collect_01 import db
from report_04.video_job import VIDEO_WALL_TIMEOUT_SEC
from report_04.phases import PLATFORM_LABELS

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"completed", "failed", "skipped"})


def list_video_steps(task_id: str) -> List[Dict[str, Any]]:
    rows = db.fetch_all(
        """
        SELECT step_key, status, message, payload_json, started_at, finished_at, updated_at
        FROM collect_phase_steps
        WHERE task_id=%s AND step_key LIKE 'step7_video_%%'
        ORDER BY step_order, step_key
        """,
        (task_id,),
    ) or []
    return [dict(r) for r in rows]


def fail_stale_video_steps(task_id: str, *, timeout_sec: int = VIDEO_WALL_TIMEOUT_SEC) -> int:
    """超时仍 running 的视频节点强制 failed，避免永远卡住发文父步与终稿。"""
    from report_04.task_store import TaskStore
    from report_04.video_job import complete_post_after_video

    store = TaskStore()
    n = 0
    for row in list_video_steps(task_id):
        if str(row.get("status") or "") != "running":
            continue
        started = row.get("started_at") or row.get("updated_at")
        if not started:
            continue
        try:
            age = time.time() - started.timestamp()
        except Exception:
            continue
        if age < timeout_sec:
            continue
        key = str(row["step_key"])
        platform = key.replace("step7_video_", "", 1) if key.startswith("step7_video_") else ""
        store.set_step_status(
            task_id,
            key,
            "failed",
            message=f"视频节点超时 {timeout_sec}s，强制失败",
            payload={"timeout": True},
        )
        if platform:
            try:
                complete_post_after_video(store, task_id, platform)
            except Exception:
                pass
        n += 1
        logger.warning("04 强制失败超时视频节点 task=%s step=%s age=%.0fs", task_id, key, age)
    return n


def video_steps_terminal(task_id: str) -> Dict[str, Any]:
    """已创建的视频节点是否全部终态（无节点视为就绪）。"""
    fail_stale_video_steps(task_id)
    rows = list_video_steps(task_id)
    open_rows = [r for r in rows if str(r.get("status") or "") not in _TERMINAL]
    return {
        "ok": len(open_rows) == 0,
        "total": len(rows),
        "open": [
            {"step_key": r.get("step_key"), "status": r.get("status")}
            for r in open_rows
        ],
        "steps": [
            {"step_key": r.get("step_key"), "status": r.get("status"), "message": r.get("message")}
            for r in rows
        ],
    }


def can_write_report_after_videos(task_id: str) -> Dict[str, Any]:
    """终稿前门禁：发文实质已齐，且视频节点须终态（失败也放行；无视频节点只看发文）。"""
    try:
        from report_04.gates import get_step_status, posts_substantively_ready

        s7 = get_step_status(task_id, "step7_posts")
        if not (posts_substantively_ready(task_id) or s7 in {"completed", "skipped"}):
            return {
                "ok": False,
                "open": [{"step_key": "step7_posts", "status": s7 or "pending"}],
                "message": "发文未齐",
            }
    except Exception as exc:
        logger.warning("写报发文门禁检查失败 task=%s: %s", task_id, exc)
        return {"ok": False, "open": [], "message": f"发文门禁检查失败:{exc}"}
    return video_steps_terminal(task_id)


def format_report_wait_hint(task_id: str) -> str:
    """视频/发文未齐时注入 Agent：禁止终稿。已齐则返回空串。"""
    gate = can_write_report_after_videos(task_id)
    if gate.get("ok"):
        return ""
    open_rows = gate.get("open") or []
    keys = ",".join(
        str(x.get("step_key") or "") for x in open_rows[:8] if isinstance(x, dict)
    )
    extra = keys or str(gate.get("message") or "未齐")
    return (
        f"【写报门禁】发文与视频都完成后才能写终稿（当前未齐：{extra}）。"
        "禁止输出「一、账号基本信息」终稿；可写步骤8/9/10；"
        "禁止结束会话；禁止同步 mcp_video2frame_*。"
    )


def collect_video_observations(task_id: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for row in list_video_steps(task_id):
        if str(row.get("status") or "") != "completed":
            continue
        key = str(row.get("step_key") or "")
        platform = key.replace("step7_video_", "", 1) if key.startswith("step7_video_") else key
        label = PLATFORM_LABELS.get(platform, platform)
        payload = row.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        text = str(payload.get("video_analysis_text") or "").strip()
        if not text:
            vrow = db.fetch_one(
                """
                SELECT video_analysis_text FROM collect_videos
                WHERE task_id=%s AND platform=%s AND analyze_status='completed'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (task_id, platform),
            )
            text = str((vrow or {}).get("video_analysis_text") or "").strip()
        if text:
            out.append({"platform": platform, "label": label, "text": text})
    return out


def format_video_section(observations: List[Dict[str, str]]) -> str:
    if not observations:
        return ""
    lines = ["### 视频观察"]
    for item in observations:
        lines.append(f"- **{item['label']}**：{item['text']}")
    return "\n".join(lines)


def inject_video_into_report(report: str, task_id: str) -> str:
    """把视频观察并入画像报告（优先插在「三、账号网络活动情况」后）。"""
    section = format_video_section(collect_video_observations(task_id))
    if not section:
        return report
    text = report or ""
    if "### 视频观察" in text:
        return text
    for marker in ("三、账号网络活动情况", "三、账号网络活动", "三、发文"):
        idx = text.find(marker)
        if idx < 0:
            continue
        line_end = text.find("\n", idx)
        if line_end < 0:
            return text + "\n\n" + section + "\n"
        return text[: line_end + 1] + "\n" + section + "\n" + text[line_end + 1 :]
    return text.rstrip() + "\n\n" + section + "\n"
