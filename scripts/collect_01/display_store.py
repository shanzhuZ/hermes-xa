"""前端展示层：field_labels.yaml → [{label, value}] 双写入 collect_display_records。"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from collect_01 import db
from collect_01.phases import PLATFORM_LABELS, post_step_key

logger = logging.getLogger(__name__)

_LABELS_PATH = Path(__file__).resolve().parent / "field_labels.yaml"

DATA_TYPE_BY_STEP = {
    "step1_input_accounts": "input_accounts",
    "step1_seed": "collect_profiles",
    "step2_cross_platform": "cross_platform_candidates",
    "step2_maigret": "cross_platform_candidates",
    "step3_profiles": "collect_profiles",
    "step3_web_search": "cross_platform_candidates",
    "step3_streams": "collect_identity_streams",
    "step4_profiles": "collect_profiles",
    "step4_text_compare": "collect_identity_streams",
    "step4_image_compare": "collect_identity_streams",
    "step5_streams": "collect_identity_streams",
    "step5_validated": "collect_validated_accounts",
    "step6_validated": "collect_validated_accounts",
    "step6_posts": "collect_posts",
    "step7_posts": "collect_posts",
    "step8_img_analysis": "report_analysis",
    "step9_context_views": "report_analysis",
    "step10_context_pii": "report_analysis",
    "step11_report": "report_analysis",
}


@lru_cache(maxsize=1)
def _load_labels() -> Dict[str, Any]:
    if not _LABELS_PATH.is_file():
        logger.warning("field_labels.yaml 不存在: %s", _LABELS_PATH)
        return {}
    with _LABELS_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _platform_label(platform: Optional[str]) -> str:
    if not platform:
        return ""
    cfg = _load_labels()
    labels = cfg.get("platform_labels") or PLATFORM_LABELS
    return str(labels.get(platform, platform))


def _field_label(data_type: str, field: str, platform: Optional[str]) -> str:
    cfg = _load_labels()
    overrides = (cfg.get("platform_overrides") or {}).get(platform or "", {})
    type_overrides = overrides.get(data_type) or {}
    if field in type_overrides:
        return str(type_overrides[field])
    type_cfg = cfg.get(data_type) or {}
    if field in type_cfg and not str(field).startswith("_"):
        return str(type_cfg[field])
    return field


def _map_value(field: str, value: Any) -> str:
    cfg = _load_labels()
    maps = cfg.get("value_maps") or {}
    field_map = maps.get(field) or {}
    if value is None:
        return ""
    if field == "platform":
        return _platform_label(str(value))
    key = str(value).strip()
    if key in field_map:
        return str(field_map[key])
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)) and field in {
        "follower_count",
        "following_count",
        "content_count",
        "view_count",
        "like_count",
        "comment_count",
        "repost_count",
    }:
        return f"{int(value):,}"
    if field == "confidence" and isinstance(value, (int, float)):
        return f"{float(value):.2%}" if float(value) <= 1 else str(value)
    text = str(value).strip()
    if len(text) > 2000:
        return text[:2000] + "…"
    return text


def build_display_fields(data_type: str, row: Dict[str, Any]) -> List[Dict[str, str]]:
    cfg = _load_labels()
    type_cfg = cfg.get(data_type) or {}
    exclude = set(type_cfg.get("_exclude") or [])
    order = list(type_cfg.get("_order") or [])
    platform = row.get("platform") or row.get("source_platform")

    keys: List[str] = []
    for k in order:
        if k not in keys and k not in exclude:
            keys.append(k)
    for k in row.keys():
        if k not in keys and k not in exclude and not str(k).startswith("_"):
            keys.append(k)

    fields: List[Dict[str, str]] = []
    for key in keys:
        if key in exclude:
            continue
        raw = row.get(key)
        if raw is None or raw == "":
            continue
        value = _map_value(key, raw)
        if not value:
            continue
        fields.append(
            {
                "label": _field_label(data_type, key, str(platform) if platform else None),
                "value": value,
            }
        )
    return fields


def build_record_title(data_type: str, row: Dict[str, Any]) -> str:
    platform = row.get("platform") or row.get("source_platform") or ""
    plabel = _platform_label(str(platform) if platform else None)
    if data_type == "collect_profiles":
        handle = row.get("account_handle") or row.get("account_id") or ""
        return f"{plabel} · @{handle}".rstrip(" · @")
    if data_type == "collect_posts":
        title = row.get("title") or row.get("content_id") or "发文"
        return f"{plabel} · {title}"
    if data_type == "cross_platform_candidates":
        handle = row.get("account_handle") or row.get("account_id") or ""
        return f"{plabel} · {handle}"
    if data_type == "collect_identity_streams":
        field = row.get("source_field") or row.get("stream_type") or "流"
        return f"{plabel} · {field}"
    if data_type == "collect_validated_accounts":
        handle = row.get("account_handle") or row.get("account_id") or ""
        seed = "（种子）" if str(row.get("is_seed")) in {"1", "True", "true"} else ""
        return f"{plabel} · @{handle}{seed}".rstrip(" · @")
    if data_type == "input_accounts":
        handle = row.get("account_handle") or ""
        return f"{plabel} · @{handle}".rstrip(" · @") if handle else plabel
    return plabel or data_type


def upsert_display_record(
    *,
    task_id: str,
    step_key: str,
    data_type: str,
    source_table: str,
    source_ref: str,
    row: Dict[str, Any],
    platform: Optional[str] = None,
    account_id: Optional[str] = None,
    stream_type: Optional[str] = None,
) -> None:
    fields = build_display_fields(data_type, row)
    if not fields:
        return
    title = build_record_title(data_type, row)
    acct = account_id or row.get("account_id") or row.get("source_account_id") or row.get("account_handle")
    if acct is not None:
        acct = str(acct).strip() or None
    db.execute(
        """
        INSERT INTO collect_display_records
          (task_id, step_key, data_type, source_table, source_ref, platform, account_id, stream_type,
           record_title, display_fields)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          data_type=VALUES(data_type),
          platform=VALUES(platform),
          account_id=VALUES(account_id),
          stream_type=VALUES(stream_type),
          record_title=VALUES(record_title),
          display_fields=VALUES(display_fields),
          updated_at=NOW(3)
        """,
        (
            task_id,
            step_key,
            data_type,
            source_table,
            str(source_ref),
            platform,
            acct,
            stream_type,
            title[:256] if title else None,
            db.json_dumps(fields),
        ),
    )


def sync_profile_display(row: Dict[str, Any], *, step_key: str) -> None:
    ref = db.fetch_one(
        """
        SELECT id, task_id, platform, account_id, account_handle, display_name, bio,
               avatar_url, profile_url, follower_count, following_count, content_count,
               verified, visibility, collect_status, collected_at
        FROM collect_profiles
        WHERE task_id=%s AND platform=%s AND account_id=%s
        """,
        (row["task_id"], row["platform"], row["account_id"]),
    )
    if not ref:
        return
    payload = dict(ref)
    upsert_display_record(
        task_id=str(ref["task_id"]),
        step_key=step_key,
        data_type="collect_profiles",
        source_table="collect_profiles",
        source_ref=str(ref["id"]),
        row=payload,
        platform=str(ref.get("platform") or ""),
    )
    # 种子资料在步骤三汇总页也要可见
    if step_key == "step1_seed":
        upsert_display_record(
            task_id=str(ref["task_id"]),
            step_key="step3_profiles",
            data_type="collect_profiles",
            source_table="collect_profiles",
            source_ref=str(ref["id"]),
            row=payload,
            platform=str(ref.get("platform") or ""),
        )


def sync_post_display(row: Dict[str, Any], *, step_key: str) -> None:
    ref = db.fetch_one(
        """
        SELECT id, task_id, platform, account_id, content_id, content_type, title,
               content_text, content_url, published_at, view_count, like_count,
               comment_count, repost_count, created_at
        FROM collect_posts
        WHERE task_id=%s AND platform=%s AND account_id=%s AND content_id=%s
        """,
        (row["task_id"], row["platform"], row["account_id"], row["content_id"]),
    )
    if not ref:
        return
    payload = dict(ref)
    platform = str(ref.get("platform") or "")
    upsert_display_record(
        task_id=str(ref["task_id"]),
        step_key=step_key,
        data_type="collect_posts",
        source_table="collect_posts",
        source_ref=str(ref["id"]),
        row=payload,
        platform=platform,
    )
    # 子平台步骤 + 步骤六汇总
    if step_key.startswith("step6_post_"):
        upsert_display_record(
            task_id=str(ref["task_id"]),
            step_key="step6_posts",
            data_type="collect_posts",
            source_table="collect_posts",
            source_ref=str(ref["id"]),
            row=payload,
            platform=platform,
        )


def sync_post_display_for_tool(row: Dict[str, Any], *, step_key: str) -> None:
    """发文工具入库：写入子步骤与汇总步骤。"""
    platform = str(row.get("platform") or "")
    if step_key.startswith("step3_post_") or step_key.startswith("step6_post_"):
        child_key = step_key
    else:
        child_key = post_step_key(platform)
    sync_post_display(row, step_key=child_key)


def sync_candidate_display(task_id: str, platform: str, account_id: str, *, step_key: str = "step2_cross_platform") -> None:
    ref = db.fetch_one(
        """
        SELECT id, task_id, platform, account_id, account_handle, confidence,
               match_strategy, status, created_at
        FROM cross_platform_candidates
        WHERE task_id=%s AND platform=%s AND account_id=%s
        ORDER BY id DESC LIMIT 1
        """,
        (task_id, platform, account_id),
    )
    if not ref:
        return
    upsert_display_record(
        task_id=str(ref["task_id"]),
        step_key=step_key,
        data_type="cross_platform_candidates",
        source_table="cross_platform_candidates",
        source_ref=str(ref["id"]),
        row=dict(ref),
        platform=str(ref.get("platform") or ""),
    )


def sync_stream_display(stream_id: str, *, step_key: str) -> None:
    ref = db.fetch_one(
        """
        SELECT stream_id, task_id, stream_type, source_platform, source_account_id,
               source_field, payload_text, payload_url, validation_status,
               validation_detail, updated_at
        FROM collect_identity_streams
        WHERE stream_id=%s
        """,
        (stream_id,),
    )
    if not ref:
        return
    upsert_display_record(
        task_id=str(ref["task_id"]),
        step_key=step_key,
        data_type="collect_identity_streams",
        source_table="collect_identity_streams",
        source_ref=str(ref["stream_id"]),
        row=dict(ref),
        platform=str(ref.get("source_platform") or ""),
        stream_type=str(ref.get("stream_type") or ""),
    )


def sync_streams_for_task(task_id: str, *, step_key: str, stream_type: Optional[str] = None) -> None:
    """批量刷新某任务下身份流的展示记录（文本/图片比对后）。"""
    if stream_type:
        rows = db.fetch_all(
            """
            SELECT stream_id FROM collect_identity_streams
            WHERE task_id=%s AND stream_type=%s
            """,
            (task_id, stream_type),
        )
    else:
        rows = db.fetch_all(
            "SELECT stream_id FROM collect_identity_streams WHERE task_id=%s",
            (task_id,),
        )
    for row in rows:
        sync_stream_display(str(row["stream_id"]), step_key=step_key)


def sync_validated_display(
    task_id: str, platform: str, account_id: str, *, step_key: str = "step5_validated"
) -> None:
    ref = db.fetch_one(
        """
        SELECT id, task_id, platform, account_id, account_handle, confidence,
               verdict, is_seed, created_at
        FROM collect_validated_accounts
        WHERE task_id=%s AND platform=%s AND account_id=%s
        """,
        (task_id, platform, account_id),
    )
    if not ref:
        return
    upsert_display_record(
        task_id=str(ref["task_id"]),
        step_key=step_key,
        data_type="collect_validated_accounts",
        source_table="collect_validated_accounts",
        source_ref=str(ref["id"]),
        row=dict(ref),
        platform=str(ref.get("platform") or ""),
    )


def backfill_task_displays(task_id: str) -> int:
    """按业务表全量回填各步骤展示数据。"""
    n = 0
    task = db.fetch_one("SELECT seed_json FROM hermes_tasks WHERE task_id=%s", (task_id,))
    seed_platform = "twitter"
    if task and task.get("seed_json"):
        try:
            import json

            seed = json.loads(task["seed_json"])
            seed_platform = seed.get("platform") or seed_platform
        except Exception:
            pass

    for row in db.fetch_all("SELECT * FROM collect_profiles WHERE task_id=%s", (task_id,)):
        sk = "step1_seed" if str(row.get("platform")) == seed_platform else "step3_profiles"
        sync_profile_display(row, step_key=sk)
        n += 1

    for row in db.fetch_all("SELECT * FROM cross_platform_candidates WHERE task_id=%s", (task_id,)):
        sync_candidate_display(task_id, row["platform"], row["account_id"], step_key="step2_cross_platform")
        n += 1

    for row in db.fetch_all("SELECT stream_id FROM collect_identity_streams WHERE task_id=%s", (task_id,)):
        sid = str(row["stream_id"])
        sync_stream_display(sid, step_key="step3_streams")
        n += 1
        ref = db.fetch_one("SELECT stream_type FROM collect_identity_streams WHERE stream_id=%s", (sid,))
        if ref and ref.get("stream_type") == "text":
            sync_stream_display(sid, step_key="step4_text_compare")
            n += 1
        elif ref and ref.get("stream_type") == "image":
            sync_stream_display(sid, step_key="step4_image_compare")
            n += 1

    for row in db.fetch_all("SELECT * FROM collect_validated_accounts WHERE task_id=%s", (task_id,)):
        sync_validated_display(task_id, row["platform"], row["account_id"], step_key="step5_validated")
        n += 1

    for row in db.fetch_all("SELECT * FROM collect_posts WHERE task_id=%s", (task_id,)):
        platform = str(row.get("platform") or "")
        sync_post_display(row, step_key=post_step_key(platform))
        n += 1

    return n
