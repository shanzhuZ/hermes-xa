"""按固定时间间隔抽帧（默认每 3 秒）。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm", ".m4v", ".3gp"}


def extract_frames_by_interval(
    video_path: str,
    output_dir: str,
    *,
    interval_sec: float = 3.0,
    quality: int = 95,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    overwrite: bool = True,
) -> Dict[str, Any]:
    """每 interval_sec 秒抽一帧，全部写入 output_dir。"""
    path = Path(video_path)
    if not path.is_file():
        raise FileNotFoundError(f"视频不存在: {video_path}")
    if interval_sec <= 0:
        raise ValueError("interval_sec 必须 > 0")

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")

    try:
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        orig_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = (total_frames / orig_fps) if orig_fps > 0 else 0.0

        t0 = float(start_time or 0.0)
        t1 = float(end_time) if end_time is not None else duration
        if t1 <= 0 and duration > 0:
            t1 = duration
        if t1 <= t0:
            raise ValueError(f"无效时间范围: start={t0} end={t1}")

        frames: List[Dict[str, Any]] = []
        t = t0
        idx = 0
        while t <= t1 + 1e-6:
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                # 末尾可能读不到，停止
                if t >= t1 - 1e-3:
                    break
                t += interval_sec
                continue

            name = f"frame_{idx:04d}_t{t:.2f}s.jpg"
            out_path = out / name
            if overwrite or not out_path.is_file():
                ok_w = cv2.imwrite(str(out_path), frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
                if not ok_w:
                    raise RuntimeError(f"写帧失败: {out_path}")
            h, w = frame.shape[:2]
            frames.append(
                {
                    "frame_index": idx,
                    "timestamp_sec": round(t, 2),
                    "local_path": str(out_path.resolve()),
                    "width": int(w),
                    "height": int(h),
                }
            )
            idx += 1
            t += interval_sec

        return {
            "frames_extracted": len(frames),
            "frames": frames,
            "output_directory": str(out.resolve()),
            "interval_sec": interval_sec,
            "video_info": {
                "path": str(path.resolve()),
                "total_frames": total_frames,
                "fps": round(orig_fps, 3),
                "resolution": f"{width}x{height}",
                "duration_sec": round(duration, 2),
                "width": width,
                "height": height,
            },
        }
    finally:
        cap.release()
