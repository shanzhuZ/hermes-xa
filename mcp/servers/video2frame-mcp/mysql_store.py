"""collect_videos / collect_video_frames 写入。"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import dbutil


def make_video_id(task_id: str, origin_url: str = "", local_hint: str = "") -> str:
    raw = f"{task_id}|{origin_url}|{local_hint}|{uuid.uuid4().hex[:8]}"
    return "vid_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def make_frame_id(video_id: str, frame_index: int) -> str:
    raw = f"{video_id}|{frame_index}"
    return "vfr_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def upsert_video_pending(row: Dict[str, Any]) -> None:
    dbutil.execute(
        """
        INSERT INTO collect_videos (
            video_id, task_id, task_type, source_type, platform, account_id, post_id, profile_id,
            origin_url, local_path, storage_status, analyze_status, frame_interval_sec
        ) VALUES (
            %(video_id)s, %(task_id)s, %(task_type)s, %(source_type)s, %(platform)s, %(account_id)s,
            %(post_id)s, %(profile_id)s, %(origin_url)s, %(local_path)s, %(storage_status)s,
            %(analyze_status)s, %(frame_interval_sec)s
        )
        ON DUPLICATE KEY UPDATE
            origin_url=VALUES(origin_url),
            local_path=VALUES(local_path),
            platform=VALUES(platform),
            account_id=VALUES(account_id),
            post_id=VALUES(post_id),
            profile_id=VALUES(profile_id),
            task_type=VALUES(task_type),
            source_type=VALUES(source_type),
            frame_interval_sec=VALUES(frame_interval_sec),
            updated_at=CURRENT_TIMESTAMP(3)
        """,
        row,
    )


def mark_video_stored(video_id: str, meta: Dict[str, Any]) -> None:
    dbutil.execute(
        """
        UPDATE collect_videos
        SET storage_status='stored',
            local_path=%s,
            content_sha256=%s,
            mime_type=%s,
            file_size=%s,
            duration_sec=%s,
            width=%s,
            height=%s,
            stored_at=CURRENT_TIMESTAMP(3),
            error_message=NULL,
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE video_id=%s
        """,
        (
            meta.get("local_path"),
            meta.get("content_sha256"),
            meta.get("mime_type"),
            meta.get("file_size"),
            meta.get("duration_sec"),
            meta.get("width"),
            meta.get("height"),
            video_id,
        ),
    )


def mark_video_failed(video_id: str, message: str) -> None:
    dbutil.execute(
        """
        UPDATE collect_videos
        SET storage_status='failed',
            analyze_status=IF(analyze_status='running','failed',analyze_status),
            error_message=%s,
            retry_count=retry_count+1,
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE video_id=%s
        """,
        ((message or "")[:1000], video_id),
    )


def mark_analyze_running(video_id: str) -> None:
    dbutil.execute(
        """
        UPDATE collect_videos
        SET analyze_status='running', error_message=NULL, updated_at=CURRENT_TIMESTAMP(3)
        WHERE video_id=%s
        """,
        (video_id,),
    )


def replace_frames(video_id: str, task_id: str, frames: List[Dict[str, Any]]) -> None:
    """覆盖写入该视频的全部帧行（含 hbase_row_key / storage_status）。"""
    dbutil.execute("DELETE FROM collect_video_frames WHERE video_id=%s", (video_id,))
    for fr in frames:
        path = fr.get("local_path")
        p = Path(path) if path else None
        size = fr.get("file_size")
        sha = fr.get("content_sha256")
        if p is not None and p.is_file():
            size = p.stat().st_size
            sha = file_sha256(str(p))
        dbutil.execute(
            """
            INSERT INTO collect_video_frames (
                frame_id, video_id, task_id, platform, account_id, post_id,
                frame_index, timestamp_sec, is_preview, sort_order,
                local_path, hbase_row_key, storage_status,
                mime_type, file_size, width, height, content_sha256,
                analyze_status, vision_text, analysis_json
            ) VALUES (
                %s,%s,%s,%s,%s,%s,
                %s,%s,%s,%s,
                %s,%s,%s,
                %s,%s,%s,%s,%s,
                %s,%s,%s
            )
            """,
            (
                fr["frame_id"],
                video_id,
                task_id,
                fr.get("platform"),
                fr.get("account_id"),
                fr.get("post_id"),
                int(fr["frame_index"]),
                float(fr["timestamp_sec"]),
                int(fr.get("is_preview") or 0),
                fr.get("sort_order"),
                path,
                fr.get("hbase_row_key"),
                fr.get("storage_status") or "pending",
                fr.get("mime_type") or "image/jpeg",
                size,
                fr.get("width"),
                fr.get("height"),
                sha,
                fr.get("analyze_status") or "pending",
                fr.get("vision_text"),
                dbutil.json_dumps(fr["analysis_json"]) if fr.get("analysis_json") is not None else None,
            ),
        )


def mark_video_analyzed(
    video_id: str,
    *,
    frame_extracted_cnt: int,
    frame_analyzed_cnt: int,
    frame_stored_cnt: int,
    analysis_json: Any,
    video_analysis_text: Optional[str],
    video_analysis_json: Any = None,
    analyze_status: str = "completed",
    error_message: Optional[str] = None,
) -> None:
    dbutil.execute(
        """
        UPDATE collect_videos
        SET analyze_status=%s,
            frame_extracted_cnt=%s,
            frame_analyzed_cnt=%s,
            frame_stored_cnt=%s,
            analysis_json=%s,
            video_analysis_text=%s,
            video_analysis_json=%s,
            error_message=%s,
            analyzed_at=CURRENT_TIMESTAMP(3),
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE video_id=%s
        """,
        (
            analyze_status,
            frame_extracted_cnt,
            frame_analyzed_cnt,
            frame_stored_cnt,
            dbutil.json_dumps(analysis_json) if analysis_json is not None else None,
            video_analysis_text,
            dbutil.json_dumps(video_analysis_json) if video_analysis_json is not None else None,
            (error_message or None) and str(error_message)[:1000],
            video_id,
        ),
    )


def pick_preview_indices(n: int, k: int = 3) -> List[int]:
    """从 n 帧中挑最多 k 个预览下标（约 25/50/75）。"""
    if n <= 0:
        return []
    if n <= k:
        return list(range(n))
    ratios = [0.25, 0.50, 0.75][:k]
    idxs = sorted({min(n - 1, max(0, int(round((n - 1) * r)))) for r in ratios})
    # 补齐到 k
    i = 0
    while len(idxs) < k and i < n:
        if i not in idxs:
            idxs.append(i)
        i += 1
    return sorted(idxs)[:k]
