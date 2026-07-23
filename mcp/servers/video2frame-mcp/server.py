#!/usr/bin/env python3
"""
video2frame-mcp — 视频下载 / 抽帧 / 多模态分析 / MySQL 入库

工具：
  - download_video: 直链或 yt-dlp（YouTube 等）下载并写 collect_videos
  - extract_frames: 按间隔抽帧（默认每 3 秒）
  - analyze_frames: 对帧列表做 VLM 分析
  - run_video_pipeline: URL/本地 → 下载 → 前2分钟抽帧 → 分析 → 入库
                       （01 采集请走后台脚本，勿 Agent 同步长等）
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 保证同目录模块可 import
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from mcp.server.fastmcp import FastMCP

from analyze import analyze_frames as do_analyze_frames
from config import (
    default_frame_interval_sec,
    max_analyze_duration_sec,
    suggested_max_videos_per_task,
    video_root_dir,
    vlm_config,
)
from download import download_video
from frames import extract_frames_by_interval
import mysql_store
from pipeline import run_video_pipeline as do_run_video_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("video2frame-mcp")

mcp = FastMCP("video2frame-mcp")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


@mcp.tool()
def download_video(
    task_id: str,
    origin_url: str,
    platform: str = "",
    account_id: str = "",
    post_id: str = "",
    profile_id: str = "",
    task_type: str = "",
    source_type: str = "post_video",
) -> str:
    """下载视频到 {HERMES_HOME}/data/video_bytes/{task_id}/{video_id}/，并写入 collect_videos。

    支持 http(s) 直链与 YouTube/Twitter 等页面链接（yt-dlp）。
    建议每个任务最多处理 3 个视频（本阶段不硬限制）。
    """
    tip = f"建议每个任务最多 {suggested_max_videos_per_task()} 个视频（本阶段不硬限制）"
    task_id = (task_id or "").strip()
    origin_url = (origin_url or "").strip()
    if not task_id or not origin_url:
        return _json({"error": "task_id 与 origin_url 必填", "tip": tip})

    video_id = mysql_store.make_video_id(task_id, origin_url, "")
    base = video_root_dir() / task_id / video_id
    base.mkdir(parents=True, exist_ok=True)
    mysql_store.upsert_video_pending(
        {
            "video_id": video_id,
            "task_id": task_id,
            "task_type": task_type or None,
            "source_type": source_type or "post_video",
            "platform": platform or None,
            "account_id": account_id or None,
            "post_id": post_id or None,
            "profile_id": profile_id or None,
            "origin_url": origin_url,
            "local_path": None,
            "storage_status": "downloading",
            "analyze_status": "pending",
            "frame_interval_sec": default_frame_interval_sec(),
        }
    )
    try:
        from dbutil import execute

        execute(
            "UPDATE collect_videos SET storage_status='downloading', updated_at=CURRENT_TIMESTAMP(3) WHERE video_id=%s",
            (video_id,),
        )
        meta = download_video(origin_url, base / "source")
        mysql_store.mark_video_stored(video_id, meta)
        return _json({"status": "stored", "tip": tip, "video_id": video_id, **meta})
    except Exception as exc:
        mysql_store.mark_video_failed(video_id, str(exc))
        return _json({"status": "failed", "tip": tip, "video_id": video_id, "error": str(exc)})


@mcp.tool()
def extract_frames(
    video_path: str,
    output_dir: str = "",
    interval_sec: float = 3.0,
    quality: int = 95,
) -> str:
    """按时间间隔抽帧（默认每 3 秒一帧），全部保存为 JPG。"""
    try:
        out = output_dir.strip() or str(Path(video_path).resolve().parent / "frames")
        result = extract_frames_by_interval(
            video_path,
            out,
            interval_sec=float(interval_sec or default_frame_interval_sec()),
            quality=int(quality),
            overwrite=True,
        )
        # 避免返回过长路径列表时可截断；这里保留摘要
        frames = result.get("frames") or []
        return _json(
            {
                "frames_extracted": result.get("frames_extracted"),
                "output_directory": result.get("output_directory"),
                "interval_sec": result.get("interval_sec"),
                "video_info": result.get("video_info"),
                "frame_sample": frames[:5],
                "frame_count": len(frames),
            }
        )
    except Exception as exc:
        return _json({"error": str(exc)})


@mcp.tool()
def analyze_frames(
    frame_paths: List[str],
    api_url: str = "",
    model: str = "",
    prompt: str = "",
    max_tokens: int = 0,
    concurrency: int = 0,
) -> str:
    """对已提取的帧图片批量调用多模态 VLM 分析。"""
    try:
        if isinstance(frame_paths, str):
            frame_paths = [frame_paths]
        cfg = vlm_config()
        results = do_analyze_frames(
            list(frame_paths),
            api_url=api_url or None,
            model=model or None,
            prompt_text=prompt or None,
            max_tokens=max_tokens or None,
            concurrency=concurrency or None,
        )
        ok = sum(1 for r in results if r.get("success"))
        return _json(
            {
                "total": len(results),
                "success_count": ok,
                "defaults": {"api_url": cfg["api_url"], "model": cfg["model"]},
                "results": results,
            }
        )
    except Exception as exc:
        return _json({"error": str(exc)})


@mcp.tool()
def run_video_pipeline(
    task_id: str,
    origin_url: str = "",
    video_path: str = "",
    platform: str = "",
    account_id: str = "",
    post_id: str = "",
    profile_id: str = "",
    task_type: str = "",
    source_type: str = "post_video",
    frame_interval_sec: float = 3.0,
    max_duration_sec: float = 0,
    quality: int = 95,
    api_url: str = "",
    model: str = "",
    prompt: str = "",
    max_tokens: int = 0,
    concurrency: int = 0,
    skip_summary: bool = False,
    persist: bool = True,
) -> str:
    """一键：下载(直链/YouTube yt-dlp) → 前2分钟每3秒抽帧 → 帧分析 → MySQL。

    建议每个任务最多 3 个视频（本阶段仅建议，不硬限制）。
    注意：01 采集请走后台脚本，勿让 Agent 同步长时间等待本工具。
    """
    try:
        result = do_run_video_pipeline(
            task_id=task_id,
            origin_url=origin_url or None,
            video_path=video_path or None,
            platform=platform or None,
            account_id=account_id or None,
            post_id=post_id or None,
            profile_id=profile_id or None,
            task_type=task_type or None,
            source_type=source_type or "post_video",
            frame_interval_sec=float(frame_interval_sec or default_frame_interval_sec()),
            max_duration_sec=float(max_duration_sec or max_analyze_duration_sec()),
            quality=int(quality),
            api_url=api_url or None,
            model=model or None,
            prompt=prompt or None,
            max_tokens=max_tokens or None,
            concurrency=concurrency or None,
            skip_summary=bool(skip_summary),
            persist=bool(persist),
        )
        return _json(result)
    except Exception as exc:
        return _json({"status": "failed", "error": str(exc)})


if __name__ == "__main__":
    # Windows UTF-8
    if os.name == "nt":
        os.environ.setdefault("PYTHONUTF8", "1")
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    mcp.run()
