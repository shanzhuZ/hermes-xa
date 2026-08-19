"""04 后台视频 runner：下载+抽帧+分析，回写 step7_video_*，并收口发文父步。

由 video_job 独立进程拉起，勿在 Hook 内同步调用。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

_SCRIPTS = Path(__file__).resolve().parent.parent
_V2F = _SCRIPTS.parent / "mcp" / "servers" / "video2frame-mcp"
for p in (_SCRIPTS, _V2F):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("report_04.video_runner")


def _assert_runtime_deps() -> None:
    logger.info("report video_runner python=%s", sys.executable)
    try:
        from PIL import Image

        logger.info("Pillow OK path=%s", getattr(Image, "__file__", "?"))
    except Exception as exc:
        raise RuntimeError(
            f"Pillow 不可用（python={sys.executable}）。"
            f"请用 HERMES_VIDEO_PYTHON / config.yaml video2frame.command 指定系统 Python。"
            f" 原始错误: {exc}"
        ) from exc
    try:
        import cv2  # noqa: F401
    except Exception as exc:
        raise RuntimeError(f"OpenCV 不可用（python={sys.executable}）: {exc}") from exc


def _mark_step(
    task_id: str,
    platform: str,
    status: str,
    message: str,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    from report_04.phases import video_step_key
    from report_04.task_store import TaskStore

    store = TaskStore()
    msg = (message or "").strip()
    if status == "failed":
        try:
            from mysql_store import public_video_error

            msg = public_video_error(msg)
        except Exception:
            msg = msg[:200]
    else:
        msg = msg[:2000]
    store.set_step_status(
        task_id,
        video_step_key(platform),
        status,
        message=msg,
        payload=payload,
    )
    if status in {"completed", "failed", "skipped"}:
        try:
            from report_04.video_job import complete_post_after_video

            complete_post_after_video(store, task_id, platform)
        except Exception as exc:
            logger.warning("视频后收口发文子步失败: %s", exc)


def run_one(
    *,
    task_id: str,
    platform: str,
    origin_url: str,
    account_id: str = "",
    post_id: str = "",
    timeout_sec: int = 600,
    max_duration_sec: float = 120.0,
    frame_interval_sec: float = 3.0,
) -> Dict[str, Any]:
    result_holder: Dict[str, Any] = {}
    error_holder: Dict[str, str] = {}

    def _work() -> None:
        try:
            _assert_runtime_deps()
            from pipeline import run_video_pipeline

            result_holder["result"] = run_video_pipeline(
                task_id=task_id,
                origin_url=origin_url,
                platform=platform or None,
                account_id=account_id or None,
                post_id=post_id or None,
                task_type="account_report",
                source_type="post_video",
                frame_interval_sec=float(frame_interval_sec),
                max_duration_sec=float(max_duration_sec),
                persist=True,
            )
        except Exception as exc:
            logger.exception("report video_runner 异常")
            error_holder["error"] = str(exc)

    th = threading.Thread(target=_work, name=f"report-video-{platform}", daemon=True)
    th.start()
    th.join(timeout=max(30, int(timeout_sec)))
    if th.is_alive():
        msg = f"视频分析超过 {timeout_sec}s，强制失败"
        logger.error(msg)
        _mark_step(task_id, platform, "failed", msg, {"timeout": True, "origin_url": origin_url})
        return {"status": "failed", "error": msg}

    if error_holder.get("error"):
        _mark_step(
            task_id,
            platform,
            "failed",
            error_holder["error"],
            {"origin_url": origin_url},
        )
        return {"status": "failed", "error": error_holder["error"]}

    result = result_holder.get("result") or {}
    status = str(result.get("status") or "failed")
    if status == "completed":
        _mark_step(
            task_id,
            platform,
            "completed",
            f"视频分析完成 frames={result.get('frame_analyzed_cnt')}",
            {
                "video_id": result.get("video_id"),
                "origin_url": origin_url,
                "video_analysis_text": (result.get("video_analysis_text") or "")[:4000],
                "elapsed_sec": result.get("elapsed_sec"),
            },
        )
    else:
        _mark_step(
            task_id,
            platform,
            "failed",
            str(result.get("error") or "视频管线失败")[:2000],
            {"video_id": result.get("video_id"), "origin_url": origin_url},
        )
    return result


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="04 后台视频分析")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--origin-url", required=True)
    parser.add_argument("--account-id", default="")
    parser.add_argument("--post-id", default="")
    parser.add_argument("--timeout-sec", type=int, default=600)
    parser.add_argument("--max-duration-sec", type=float, default=120.0)
    parser.add_argument("--frame-interval-sec", type=float, default=3.0)
    args = parser.parse_args(argv)

    started = time.time()
    logger.info(
        "report video_runner start task=%s platform=%s url=%s max_dur=%s interval=%s",
        args.task_id,
        args.platform,
        args.origin_url[:160],
        args.max_duration_sec,
        args.frame_interval_sec,
    )
    try:
        out = run_one(
            task_id=args.task_id,
            platform=args.platform,
            origin_url=args.origin_url,
            account_id=args.account_id,
            post_id=args.post_id,
            timeout_sec=args.timeout_sec,
            max_duration_sec=args.max_duration_sec,
            frame_interval_sec=args.frame_interval_sec,
        )
        print(json.dumps(out, ensure_ascii=False, default=str))
        ok = str(out.get("status") or "") == "completed"
        logger.info("report video_runner done ok=%s elapsed=%.1fs", ok, time.time() - started)
        return 0 if ok else 1
    except Exception as exc:
        logger.exception("report video_runner 顶层失败")
        try:
            _mark_step(args.task_id, args.platform, "failed", str(exc))
        except Exception:
            pass
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
