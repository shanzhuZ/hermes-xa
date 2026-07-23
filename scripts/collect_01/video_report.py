"""视频步骤门禁 / 等待 / 报告片段。"""

from __future__ import annotations

import argparse
import json
import logging
import time
from typing import Any, Dict, List, Optional

from collect_01 import db
from collect_01.gates import get_step_status
from collect_01.phases import PLATFORM_LABELS, video_step_key
from collect_01.video_job import VIDEO_WALL_TIMEOUT_SEC

logger = logging.getLogger(__name__)

_TERMINAL = frozenset({"completed", "failed", "skipped"})


def list_video_steps(task_id: str) -> List[Dict[str, Any]]:
    rows = db.fetch_all(
        """
        SELECT step_key, status, message, payload_json, started_at, finished_at, updated_at
        FROM collect_phase_steps
        WHERE task_id=%s AND step_key LIKE 'step6_video_%%'
        ORDER BY step_order, step_key
        """,
        (task_id,),
    ) or []
    return [dict(r) for r in rows]


def fail_stale_video_steps(task_id: str, *, timeout_sec: int = VIDEO_WALL_TIMEOUT_SEC) -> int:
    """将超时仍 running 的视频节点标失败，避免永久卡报告。"""
    from collect_01.task_store import TaskStore

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
        store.set_step_status(
            task_id,
            key,
            "failed",
            message=f"视频节点超时 {timeout_sec}s，强制失败",
            payload={"timeout": True},
        )
        n += 1
        logger.warning("强制失败超时视频节点 task=%s step=%s age=%.0fs", task_id, key, age)
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
    """步骤7前门禁：视频节点须终态（失败也放行）。"""
    return video_steps_terminal(task_id)


def collect_video_observations(task_id: str) -> List[Dict[str, str]]:
    """收集成功视频的分析文本，供报告写入。"""
    out: List[Dict[str, str]] = []
    for row in list_video_steps(task_id):
        if str(row.get("status") or "") != "completed":
            continue
        key = str(row.get("step_key") or "")
        platform = key.replace("step6_video_", "", 1) if key.startswith("step6_video_") else key
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
            # 回落 collect_videos
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
    """把视频观察并入三节报告（插在「三、发文」标题后）。"""
    section = format_video_section(collect_video_observations(task_id))
    if not section:
        return report
    text = report or ""
    if "### 视频观察" in text:
        return text
    marker = "三、发文"
    idx = text.find(marker)
    if idx < 0:
        return text.rstrip() + "\n\n" + section + "\n"
    # 找到该节标题行末尾
    line_end = text.find("\n", idx)
    if line_end < 0:
        return text + "\n\n" + section + "\n"
    return text[: line_end + 1] + "\n" + section + "\n" + text[line_end + 1 :]


def wait_video_steps(
    task_id: str,
    *,
    timeout_sec: int = VIDEO_WALL_TIMEOUT_SEC,
    poll_sec: float = 5.0,
) -> Dict[str, Any]:
    """供 Agent terminal：等到视频终态或超时强制失败。"""
    deadline = time.time() + max(10, int(timeout_sec))
    while True:
        gate = video_steps_terminal(task_id)
        if gate.get("ok"):
            obs = collect_video_observations(task_id)
            return {
                "ok": True,
                "gate": gate,
                "observations": obs,
                "section_markdown": format_video_section(obs),
            }
        if time.time() >= deadline:
            from collect_01.task_store import TaskStore

            store = TaskStore()
            for row in list_video_steps(task_id):
                if str(row.get("status") or "") in {"pending", "running"}:
                    store.set_step_status(
                        task_id,
                        str(row["step_key"]),
                        "failed",
                        message="等待超时，强制失败",
                        payload={"timeout": True},
                    )
            gate = video_steps_terminal(task_id)
            obs = collect_video_observations(task_id)
            return {
                "ok": True,
                "timed_out": True,
                "gate": gate,
                "observations": obs,
                "section_markdown": format_video_section(obs),
            }
        time.sleep(max(1.0, float(poll_sec)))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="等待 01 视频节点终态")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--timeout-sec", type=int, default=VIDEO_WALL_TIMEOUT_SEC)
    parser.add_argument("--poll-sec", type=float, default=5.0)
    parser.add_argument("--check-only", action="store_true", help="只检查不阻塞")
    args = parser.parse_args(argv)
    if args.check_only:
        out = can_write_report_after_videos(args.task_id)
        out["observations"] = collect_video_observations(args.task_id)
        out["section_markdown"] = format_video_section(out["observations"])
    else:
        out = wait_video_steps(
            args.task_id,
            timeout_sec=args.timeout_sec,
            poll_sec=args.poll_sec,
        )
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
