"""视频管线：下载 → 抽帧 → 全量分析 → 入库。"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from analyze import analyze_frames, summarize_video_from_frame_texts
from config import (
    default_frame_interval_sec,
    hermes_home,
    max_analyze_duration_sec,
    suggested_max_videos_per_task,
    video_root_dir,
    vlm_config,
)
from download import download_video
from frames import extract_frames_by_interval
import mysql_store

logger = logging.getLogger(__name__)


def _put_frame_to_hbase(task_id: str, local_path: str) -> Dict[str, Any]:
    """上传抽帧 jpg 到 HBase（frm: 前缀）；成功返回 row_key，失败不抛死。"""
    import hashlib
    import sys

    scripts = str((hermes_home() / "scripts").resolve())
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from image_pipeline.hbase_store import put_frame  # noqa: WPS433

    p = Path(local_path)
    if not p.is_file():
        return {"ok": False, "error": "frame_file_missing"}
    raw = p.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    try:
        row_key = put_frame(
            task_id,
            sha,
            raw,
            mime_type="image/jpeg",
            origin_url=str(p),
            file_size=len(raw),
        )
        return {"ok": True, "hbase_row_key": row_key, "content_sha256": sha, "file_size": len(raw)}
    except Exception as exc:
        logger.warning("抽帧 HBase 上传失败 path=%s err=%s", local_path, exc)
        return {"ok": False, "error": str(exc), "content_sha256": sha, "file_size": len(raw)}


def _task_video_dir(task_id: str, video_id: str) -> Path:
    return video_root_dir() / task_id / video_id


def run_video_pipeline(
    *,
    task_id: str,
    origin_url: Optional[str] = None,
    video_path: Optional[str] = None,
    platform: Optional[str] = None,
    account_id: Optional[str] = None,
    post_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    task_type: Optional[str] = None,
    source_type: str = "post_video",
    frame_interval_sec: Optional[float] = None,
    max_duration_sec: Optional[float] = None,
    quality: int = 95,
    api_url: Optional[str] = None,
    model: Optional[str] = None,
    prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    concurrency: Optional[int] = None,
    skip_summary: bool = False,
    persist: bool = True,
) -> Dict[str, Any]:
    """
    一键管线。
    - 给 origin_url：直链或 yt-dlp（YouTube/页面）下载
    - 或给 video_path：直接用本地文件
    - 默认只抽前 max_duration_sec 秒（默认 120），每 interval 秒一帧
    建议：每个任务视频数 ≤ suggested_max_videos_per_task()（本阶段不硬限制）
    """
    started = time.time()
    task_id = (task_id or "").strip()
    if not task_id:
        raise ValueError("task_id 必填")
    if not origin_url and not video_path:
        raise ValueError("origin_url 与 video_path 至少提供一个")

    interval = float(frame_interval_sec if frame_interval_sec is not None else default_frame_interval_sec())
    max_dur = float(max_duration_sec if max_duration_sec is not None else max_analyze_duration_sec())
    video_id = mysql_store.make_video_id(task_id, origin_url or "", video_path or "")
    base = _task_video_dir(task_id, video_id)
    frames_dir = base / "frames"
    base.mkdir(parents=True, exist_ok=True)

    tip = (
        f"建议每个任务最多处理 {suggested_max_videos_per_task()} 个视频（本阶段不硬限制）；"
        f"仅分析前 {max_dur:.0f}s，每 {interval}s 一帧"
    )

    row = {
        "video_id": video_id,
        "task_id": task_id,
        "task_type": task_type,
        "source_type": source_type or "post_video",
        "platform": platform,
        "account_id": account_id,
        "post_id": post_id,
        "profile_id": profile_id,
        "origin_url": origin_url,
        "local_path": None,
        "storage_status": "pending",
        "analyze_status": "pending",
        "frame_interval_sec": interval,
    }
    if persist:
        mysql_store.upsert_video_pending(row)

    try:
        # 1) 下载或定位本地视频
        local_video: Path
        dl_meta: Dict[str, Any] = {}
        if origin_url:
            if persist:
                from dbutil import execute

                execute(
                    "UPDATE collect_videos SET storage_status='downloading', updated_at=CURRENT_TIMESTAMP(3) WHERE video_id=%s",
                    (video_id,),
                )
            dest = base / "source"
            dl_meta = download_video(origin_url, dest, max_duration_sec=max_dur)
            local_video = Path(dl_meta["local_path"])
            logger.info(
                "下载完成 size=%s section=%s path=%s",
                dl_meta.get("file_size"),
                dl_meta.get("download_section_sec"),
                str(local_video)[:160],
            )
        else:
            local_video = Path(video_path).resolve()
            if not local_video.is_file():
                raise FileNotFoundError(f"本地视频不存在: {video_path}")
            dl_meta = {
                "local_path": str(local_video),
                "file_size": local_video.stat().st_size,
                "content_sha256": mysql_store.file_sha256(str(local_video)),
                "mime_type": "video/mp4",
                "origin_url": origin_url,
            }
            # 拷贝一份到标准目录，便于统一管理
            target = base / f"source{local_video.suffix or '.mp4'}"
            if local_video.resolve() != target.resolve():
                target.write_bytes(local_video.read_bytes())
                local_video = target
                dl_meta["local_path"] = str(local_video)
                dl_meta["file_size"] = local_video.stat().st_size
                dl_meta["content_sha256"] = mysql_store.file_sha256(str(local_video))

        # 2) 抽帧：仅前 max_dur 秒，每 interval 秒一帧
        extract = extract_frames_by_interval(
            str(local_video),
            str(frames_dir),
            interval_sec=interval,
            quality=quality,
            start_time=0.0,
            end_time=max_dur,
            overwrite=True,
        )
        vinfo = extract.get("video_info") or {}
        dl_meta.update(
            {
                "duration_sec": vinfo.get("duration_sec"),
                "width": vinfo.get("width"),
                "height": vinfo.get("height"),
            }
        )
        if persist:
            mysql_store.mark_video_stored(video_id, dl_meta)
            mysql_store.mark_analyze_running(video_id)

        frame_rows_raw: List[Dict[str, Any]] = extract.get("frames") or []
        frame_paths = [f["local_path"] for f in frame_rows_raw]

        # 3) 全量分析
        analysis = analyze_frames(
            frame_paths,
            api_url=api_url,
            model=model,
            prompt_text=prompt,
            max_tokens=max_tokens,
            concurrency=concurrency,
        )
        by_path = {a.get("frame_path"): a for a in analysis}

        preview_idxs = set(mysql_store.pick_preview_indices(len(frame_rows_raw), 3))
        frame_db_rows: List[Dict[str, Any]] = []
        analysis_list: List[Dict[str, Any]] = []
        ok_for_summary: List[Dict[str, Any]] = []

        for fr in frame_rows_raw:
            path = fr["local_path"]
            ar = by_path.get(path) or {}
            success = bool(ar.get("success"))
            desc = ar.get("description") if success else None
            err = ar.get("error")
            item = {
                "frame": Path(path).name,
                "frame_index": fr["frame_index"],
                "timestamp_sec": fr["timestamp_sec"],
                "success": success,
                "description": desc,
                "error": err,
            }
            analysis_list.append(item)
            if success:
                ok_for_summary.append(
                    {
                        "success": True,
                        "timestamp_sec": fr["timestamp_sec"],
                        "description": desc,
                    }
                )

            idx = int(fr["frame_index"])
            is_preview = 1 if idx in preview_idxs else 0
            sort_order = (sorted(preview_idxs).index(idx) + 1) if is_preview else None
            row = {
                "frame_id": mysql_store.make_frame_id(video_id, idx),
                "platform": platform,
                "account_id": account_id,
                "post_id": post_id,
                "frame_index": idx,
                "timestamp_sec": fr["timestamp_sec"],
                "is_preview": is_preview,
                "sort_order": sort_order,
                "local_path": path,
                "mime_type": "image/jpeg",
                "width": fr.get("width"),
                "height": fr.get("height"),
                "analyze_status": "completed" if success else "failed",
                "vision_text": desc,
                "analysis_json": {"error": err} if err else {"description": desc},
                "storage_status": "pending",
                "hbase_row_key": None,
            }
            # 抽帧进 HBase；成功则删本地 jpg（mp4 保留）
            put = _put_frame_to_hbase(task_id, path)
            if put.get("ok") and put.get("hbase_row_key"):
                row["hbase_row_key"] = put["hbase_row_key"]
                row["storage_status"] = "stored"
                row["content_sha256"] = put.get("content_sha256")
                row["file_size"] = put.get("file_size")
                try:
                    fp = Path(path)
                    if fp.is_file():
                        fp.unlink()
                    row["local_path"] = None
                except Exception as unlink_exc:
                    logger.warning("删除本地抽帧失败 path=%s err=%s", path, unlink_exc)
            else:
                row["storage_status"] = "failed"
                if put.get("content_sha256"):
                    row["content_sha256"] = put.get("content_sha256")
                if put.get("file_size"):
                    row["file_size"] = put.get("file_size")
            frame_db_rows.append(row)

        if persist:
            mysql_store.replace_frames(video_id, task_id, frame_db_rows)

        # 4) 整段视频摘要
        video_analysis_text = None
        video_analysis_json = None
        if not skip_summary:
            summary = summarize_video_from_frame_texts(
                ok_for_summary,
                api_url=api_url,
                model=model,
                max_tokens=max_tokens,
            )
            if summary.get("success"):
                video_analysis_text = summary.get("text")
                video_analysis_json = {"source": "frame_text_summary", "ok": True}
            else:
                video_analysis_json = {"source": "frame_text_summary", "ok": False, "error": summary.get("error")}

        analyzed_cnt = sum(1 for x in analysis_list if x.get("success"))
        stored_cnt = sum(1 for r in frame_db_rows if r.get("storage_status") == "stored")
        if persist:
            mysql_store.mark_video_analyzed(
                video_id,
                frame_extracted_cnt=len(frame_rows_raw),
                frame_analyzed_cnt=analyzed_cnt,
                frame_stored_cnt=stored_cnt,
                analysis_json=analysis_list,
                video_analysis_text=video_analysis_text,
                video_analysis_json=video_analysis_json,
                analyze_status="completed" if analyzed_cnt > 0 else "failed",
                error_message=None if analyzed_cnt > 0 else "全部帧分析失败",
            )

        # 落盘 analysis.json 备份
        result_path = base / "analysis.json"
        result_obj = {
            "video_id": video_id,
            "task_id": task_id,
            "tip": tip,
            "local_video": str(local_video),
            "frame_interval_sec": interval,
            "max_duration_sec": max_dur,
            "frame_extracted_cnt": len(frame_rows_raw),
            "frame_analyzed_cnt": analyzed_cnt,
            "preview_frame_indexes": sorted(preview_idxs),
            "analysis": analysis_list,
            "video_analysis_text": video_analysis_text,
            "video_info": vinfo,
            "vlm": {
                "api_url": api_url or vlm_config()["api_url"],
                "model": model or vlm_config()["model"],
            },
            "elapsed_sec": round(time.time() - started, 2),
        }
        result_path.write_text(json.dumps(result_obj, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "status": "completed",
            "tip": tip,
            "video_id": video_id,
            "task_id": task_id,
            "local_path": str(local_video),
            "frames_dir": str(frames_dir),
            "result_file": str(result_path),
            "frame_extracted_cnt": len(frame_rows_raw),
            "frame_analyzed_cnt": analyzed_cnt,
            "frame_stored_cnt": len(frame_rows_raw),
            "preview_frame_indexes": sorted(preview_idxs),
            "video_analysis_text": video_analysis_text,
            "elapsed_sec": round(time.time() - started, 2),
        }
    except Exception as exc:
        logger.exception("视频管线失败 video_id=%s", video_id)
        if persist:
            try:
                mysql_store.mark_video_failed(video_id, str(exc))
            except Exception:
                pass
        return {
            "status": "failed",
            "tip": tip,
            "video_id": video_id,
            "task_id": task_id,
            "error": str(exc),
            "elapsed_sec": round(time.time() - started, 2),
        }
