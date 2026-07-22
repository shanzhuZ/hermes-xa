"""03 账号核查 — 步骤完成门禁。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from collect_01 import db
from verify_03.phases import TASK_TYPE


def get_step_status(task_id: str, step_key: str) -> Optional[str]:
    row = db.fetch_one(
        "SELECT status FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, step_key),
    )
    return str((row or {}).get("status") or "").strip() or None


def _load_input_accounts(task_id: str) -> List[Dict[str, Any]]:
    row = db.fetch_one("SELECT seed_json FROM hermes_tasks WHERE task_id=%s", (task_id,))
    if not row:
        return []
    try:
        seed = json.loads(row.get("seed_json") or "{}")
    except json.JSONDecodeError:
        return []
    accounts = seed.get("input_accounts")
    return accounts if isinstance(accounts, list) else []


def count_image_streams(task_id: str) -> int:
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image'
        """,
        (task_id,),
    )
    return int((row or {}).get("c") or 0)


def count_image_streams_processed(task_id: str) -> int:
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image'
          AND validation_status IN ('processed', 'pass', 'fail')
        """,
        (task_id,),
    )
    return int((row or {}).get("c") or 0)


def is_image_compare_ready(task_id: str) -> bool:
    n_img = count_image_streams(task_id)
    if n_img == 0:
        return True
    return count_image_streams_processed(task_id) >= n_img


def count_vision_tool_calls(task_id: str) -> int:
    """成功或失败的 vision 工具调用次数（只要发起过就算尝试）。"""
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s
          AND tool_name IN ('vision_analyze', 'mcp_vision_analyze')
          AND status IN ('success', 'error')
        """,
        (task_id,),
    )
    return int((row or {}).get("c") or 0)


def count_image_streams_vision_done(task_id: str) -> int:
    """真正做过 Vision 收口的图片流（排除终稿兜底假 fail）。"""
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image'
          AND (
            validation_status='pass'
            OR (
              validation_status='fail'
              AND IFNULL(validation_detail,'') NOT LIKE %s
              AND IFNULL(validation_detail,'') NOT LIKE %s
            )
          )
        """,
        (task_id, "%终稿%", "%未完成 vision 兜底%"),
    )
    return int((row or {}).get("c") or 0)


def is_vision_gate_ready(task_id: str) -> Dict[str, Any]:
    """终稿门禁：有头像图片流时，必须已发起 Vision（禁止常识编造 3.2）。

    通过条件（满足其一）：
    - 无 image 流 → 放行
    - vision 工具调用次数 >= 图片流数（已真实调过，即使 URL 回写偶发未匹配）
    - 或：无 pending，且已 Vision 收口的流数 >= 图片流数
    """
    n_img = count_image_streams(task_id)
    if n_img <= 0:
        return {"ok": True, "message": "无图片流，跳过 Vision 门禁", "n_img": 0}

    row_pending = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image' AND validation_status='pending'
        """,
        (task_id,),
    )
    n_pending = int((row_pending or {}).get("c") or 0)
    n_vision_tools = count_vision_tool_calls(task_id)
    n_done = count_image_streams_vision_done(task_id)

    if n_vision_tools >= n_img or (n_pending == 0 and n_done >= n_img):
        return {
            "ok": True,
            "message": "Vision 门禁通过",
            "n_img": n_img,
            "n_pending": n_pending,
            "n_vision_tools": n_vision_tools,
            "n_done": n_done,
        }
    return {
        "ok": False,
        "message": (
            f"Vision 未完成：图片流={n_img} pending={n_pending} "
            f"vision工具={n_vision_tools} 已收口={n_done}；"
            f"须对每个头像调用 vision_analyze 后再输出三节终稿"
        ),
        "n_img": n_img,
        "n_pending": n_pending,
        "n_vision_tools": n_vision_tools,
        "n_done": n_done,
    }


def can_advance_to_step4(task_id: str) -> Dict[str, Any]:
    """文本/图片流分析前：步骤1完成且步骤3（主页+发文）完成。"""
    s1 = get_step_status(task_id, "step1_input_accounts")
    if s1 != "completed":
        return {"ok": False, "message": f"step1_input_accounts={s1 or 'pending'}"}
    s3 = get_step_status(task_id, "step3_profiles")
    if s3 != "completed":
        return {"ok": False, "message": f"step3_profiles={s3 or 'pending'}"}
    return {"ok": True, "message": "满足流分析推进条件"}


def can_advance_to_step45(task_id: str) -> Dict[str, Any]:
    """与 01 命名对齐：step4 对比前须 step3_streams 完成。"""
    gate = can_advance_to_step4(task_id)
    if not gate.get("ok"):
        return gate
    s_stream = get_step_status(task_id, "step3_streams")
    if s_stream != "completed":
        return {"ok": False, "message": f"step3_streams={s_stream or 'pending'}"}
    return {"ok": True, "message": "满足 step4 推进条件"}


def profile_attempt_done(task_id: str, platform: str, handle: str) -> bool:
    """该输入账号是否已有 profile 采集尝试（成功或明确空）。"""
    handle_l = handle.lower().lstrip("@")
    row = db.fetch_one(
        """
        SELECT id, collect_status FROM collect_profiles
        WHERE task_id=%s AND platform=%s
          AND (LOWER(account_handle)=LOWER(%s) OR LOWER(account_id)=LOWER(%s))
        LIMIT 1
        """,
        (task_id, platform, handle_l, handle_l),
    )
    if row:
        return True
    # 工具失败也可能无 profile 行：查工具调用
    tool_row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND status IN ('success', 'error')
          AND phase='step3_profiles'
          AND (tool_args LIKE %s OR tool_args LIKE %s)
        """,
        (task_id, f"%{handle_l}%", f"%{platform}%"),
    )
    return int((tool_row or {}).get("c") or 0) > 0


def all_input_profiles_attempted(task_id: str) -> bool:
    accounts = _load_input_accounts(task_id)
    if not accounts:
        return False
    rows = db.fetch_all(
        """
        SELECT step_key, status FROM collect_phase_steps
        WHERE task_id=%s AND parent_step_key='step3_profiles'
        """,
        (task_id,),
    )
    if rows:
        for row in rows:
            st = str(row.get("status") or "pending")
            if st in {"pending", "running"}:
                return False
        return True
    for acc in accounts:
        plat = str(acc.get("platform") or "")
        handle = str(acc.get("account_handle") or acc.get("account_id") or "")
        if not profile_attempt_done(task_id, plat, handle):
            return False
    return True
