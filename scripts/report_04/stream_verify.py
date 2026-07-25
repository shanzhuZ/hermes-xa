"""4.1 系统侧模型核验：文本流推理结论 + 图片流对比结论。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from collect_01 import db
from report_04.llm_chat import chat_text

logger = logging.getLogger(__name__)


def _extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _seed_meta(task_id: str) -> Tuple[str, str]:
    task = db.fetch_one("SELECT seed_json FROM hermes_tasks WHERE task_id=%s", (task_id,))
    seed = {}
    try:
        seed = json.loads((task or {}).get("seed_json") or "{}")
    except Exception:
        seed = {}
    platform = str(seed.get("platform") or "twitter").strip() or "twitter"
    handle = (
        str(seed.get("account_handle") or seed.get("account_hint") or "")
        .lower()
        .strip()
        .lstrip("@")
    )
    return platform, handle


def run_text_model_verify(task_id: str) -> Dict[str, Any]:
    """
    对文本身份流做模型核验：写逐条 validation_detail + 总结论。
    返回 payload 片段：conclusion / modelAnalysis / items 等。
    """
    seed_platform, seed_handle = _seed_meta(task_id)
    rows = db.fetch_all(
        """
        SELECT stream_id, source_platform, source_account_id, source_field,
               payload_text, validation_status
        FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='text'
        ORDER BY source_platform, source_field
        """,
        (task_id,),
    )
    if not rows:
        return {"ok": False, "error": "no_text_streams"}

    lines = []
    for r in rows:
        lines.append(
            {
                "stream_id": r["stream_id"],
                "platform": r.get("source_platform"),
                "field": r.get("source_field"),
                "text": (r.get("payload_text") or "")[:500],
                "rule_status": r.get("validation_status") or "pending",
            }
        )
    prompt = (
        "你是跨平台账号身份核验分析员。请基于种子账号与各平台文本流，给出可落地的核验判断。\n"
        f"种子平台：{seed_platform}；种子 handle：{seed_handle or '(未知)'}。\n"
        "规则比对状态仅供参考，你需要结合语义再判断。\n"
        "输出必须是 JSON（不要 markdown），格式：\n"
        "{\n"
        '  "conclusion": "一段完整中文结论，必须包含「经过…所以…」式推理，说明哪些字段一致/不一致及最终是否同一主体倾向",\n'
        '  "items": [\n'
        '    {"stream_id":"...","status":"pass|fail|uncertain","reason":"一句中文理由"}\n'
        "  ]\n"
        "}\n"
        f"输入流列表：\n{json.dumps(lines, ensure_ascii=False)}"
    )
    result = chat_text(prompt, max_tokens=1800)
    if not result.get("success"):
        # 兜底：用规则结果拼可读结论
        pass_n = sum(1 for r in rows if (r.get("validation_status") or "") == "pass")
        conclusion = (
            f"经过对 {len(rows)} 条文本流的规则比对（种子平台 {seed_platform}），"
            f"通过 {pass_n}/{len(rows)} 条；因模型调用失败未能补充语义推理"
            f"（{result.get('error')}），所以当前以规则比对结果为准。"
        )
        for r in rows:
            st = r.get("validation_status") or "pending"
            detail = (
                f"经过规则比对判定为 {st}，"
                f"字段 {r.get('source_field')} 相对种子平台 {seed_platform}；"
                f"所以维持该状态（模型未返回）。"
            )
            db.execute(
                """
                UPDATE collect_identity_streams
                SET validation_detail=%s, updated_at=NOW(3)
                WHERE stream_id=%s
                """,
                (detail[:1000], r["stream_id"]),
            )
        return {
            "ok": True,
            "source": "rule_fallback",
            "conclusion": conclusion,
            "modelAnalysis": conclusion,
            "error": result.get("error"),
        }

    parsed = _extract_json_obj(str(result.get("text") or ""))
    conclusion = ""
    items: List[Dict[str, Any]] = []
    if isinstance(parsed, dict):
        conclusion = str(parsed.get("conclusion") or "").strip()
        raw_items = parsed.get("items") or []
        if isinstance(raw_items, list):
            items = [x for x in raw_items if isinstance(x, dict)]
    if not conclusion:
        # 模型未按 JSON 返回时，整段作为结论
        conclusion = str(result.get("text") or "").strip()[:4000]
    # 强制结论里有「经过/所以」可读推理痕迹（没有则包一层）
    if "经过" not in conclusion or "所以" not in conclusion:
        conclusion = (
            f"经过对各平台文本流（display_name/handle/bio）与种子 {seed_platform}"
            f"/{seed_handle or '未知'} 的比对，{conclusion}。"
            f"所以当前文本流核验以该判断为准。"
        )

    by_id = {str(it.get("stream_id") or ""): it for it in items}
    for r in rows:
        sid = r["stream_id"]
        it = by_id.get(sid) or {}
        st = str(it.get("status") or r.get("validation_status") or "uncertain").lower()
        if st not in {"pass", "fail", "uncertain", "pending"}:
            st = "uncertain"
        reason = str(it.get("reason") or "").strip()
        if not reason:
            reason = f"模型未给出该条独立理由，沿用规则状态 {r.get('validation_status')}"
        detail = f"经过模型核验：{reason}。所以判定为 {st}。"
        # uncertain 不覆盖已有 pass/fail 规则状态为 fail；仅写 detail
        if st in {"pass", "fail"}:
            db.execute(
                """
                UPDATE collect_identity_streams
                SET validation_status=%s, validation_detail=%s, updated_at=NOW(3)
                WHERE stream_id=%s
                """,
                (st, detail[:1000], sid),
            )
        else:
            db.execute(
                """
                UPDATE collect_identity_streams
                SET validation_detail=%s, updated_at=NOW(3)
                WHERE stream_id=%s
                """,
                (detail[:1000], sid),
            )

    try:
        from collect_01.display_store import sync_streams_for_task
        from report_04.phases import STREAM_TEXT_STEP_KEY

        sync_streams_for_task(task_id, step_key=STREAM_TEXT_STEP_KEY, stream_type="text")
    except Exception as exc:
        logger.warning("文本模型核验后展示层双写失败: %s", exc)

    return {
        "ok": True,
        "source": "model_verify",
        "conclusion": conclusion,
        "modelAnalysis": conclusion,
        "itemCount": len(items),
    }


def _match_image_row(url: str, images: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    u = (url or "").strip().split("?")[0].lower()
    if not u:
        return None
    for img in images:
        ou = str(img.get("origin_url") or "").strip().split("?")[0].lower()
        if not ou:
            continue
        if ou == u or u in ou or ou in u:
            return img
        # twitter 尺寸后缀弱匹配
        nu = re.sub(r"_(?:normal|bigger|mini|200x200|400x400)(\.(?:jpe?g|png|webp))$", r"\1", u)
        no = re.sub(r"_(?:normal|bigger|mini|200x200|400x400)(\.(?:jpe?g|png|webp))$", r"\1", ou)
        if nu and nu == no:
            return img
    return None


def run_image_model_compare(task_id: str) -> Dict[str, Any]:
    """
    用 collect_images.vision_text 对比图片身份流，写 validation_status/detail。
    """
    seed_platform, seed_handle = _seed_meta(task_id)
    streams = db.fetch_all(
        """
        SELECT stream_id, source_platform, source_account_id, source_field, payload_url
        FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image'
        ORDER BY source_platform, source_field
        """,
        (task_id,),
    )
    if not streams:
        return {"ok": True, "skipped": True, "reason": "no_image_streams"}

    images = db.fetch_all(
        """
        SELECT image_id, platform, account_id, origin_url, vision_text, analyze_status, source_type
        FROM collect_images
        WHERE task_id=%s AND storage_status='stored'
        """,
        (task_id,),
    )
    items = []
    seed_vision = ""
    for s in streams:
        img = _match_image_row(str(s.get("payload_url") or ""), images)
        vision = ""
        image_id = None
        if img:
            vision = str(img.get("vision_text") or "").strip()
            image_id = img.get("image_id")
            if (s.get("source_platform") or "") == seed_platform and vision:
                seed_vision = vision
        items.append(
            {
                "stream_id": s["stream_id"],
                "platform": s.get("source_platform"),
                "field": s.get("source_field"),
                "url": s.get("payload_url"),
                "image_id": image_id,
                "vision_text": vision[:800],
            }
        )

    prompt = (
        "你是跨平台账号头像/封面核验分析员。请根据各平台图片视觉分析结果，判断是否像同一主体。\n"
        f"种子平台：{seed_platform}；种子 handle：{seed_handle or '(未知)'}。\n"
        f"种子平台视觉描述：{(seed_vision or '(暂无)')[:800]}\n"
        "输出必须是 JSON（不要 markdown）：\n"
        "{\n"
        '  "conclusion": "一段完整中文对比结论，必须包含「经过…所以…」式推理",\n'
        '  "items": [\n'
        '    {"stream_id":"...","status":"pass|fail|uncertain","reason":"一句中文理由"}\n'
        "  ]\n"
        "}\n"
        f"输入：\n{json.dumps(items, ensure_ascii=False)}"
    )
    result = chat_text(prompt, max_tokens=1600)
    conclusion = ""
    parsed_items: List[Dict[str, Any]] = []
    if result.get("success"):
        parsed = _extract_json_obj(str(result.get("text") or ""))
        if isinstance(parsed, dict):
            conclusion = str(parsed.get("conclusion") or "").strip()
            raw = parsed.get("items") or []
            if isinstance(raw, list):
                parsed_items = [x for x in raw if isinstance(x, dict)]
        if not conclusion:
            conclusion = str(result.get("text") or "").strip()[:4000]
    if not conclusion:
        analyzed_n = sum(1 for it in items if it.get("vision_text"))
        conclusion = (
            f"经过对 {len(streams)} 条图片流与已分析图片（{analyzed_n} 张有视觉描述）的对照，"
            f"模型调用失败（{result.get('error')}）；"
            f"所以暂以「是否已产出视觉分析」作为弱证据，待人工复核。"
        )

    if "经过" not in conclusion or "所以" not in conclusion:
        conclusion = (
            f"经过对种子平台 {seed_platform} 与其它平台头像视觉描述的比对，{conclusion}。"
            f"所以当前图片流核验以该判断为准。"
        )

    by_id = {str(it.get("stream_id") or ""): it for it in parsed_items}
    for s in streams:
        sid = s["stream_id"]
        it = by_id.get(sid) or {}
        matched = _match_image_row(str(s.get("payload_url") or ""), images)
        has_vision = bool(matched and (matched.get("vision_text") or "").strip())
        st = str(it.get("status") or "").lower()
        if st not in {"pass", "fail"}:
            # 库枚举以 pass/fail/pending 为主；无明确结论时：有视觉描述弱通过，否则 fail
            st = "pass" if has_vision else "fail"
        reason = str(it.get("reason") or "").strip()
        if not reason:
            reason = (
                "已有视觉分析，纳入跨平台弱一致判断"
                if st == "pass"
                else "缺少视觉分析或模型判定不一致"
            )
        detail = f"经过图片对比：{reason}。所以判定为 {st}。"
        db.execute(
            """
            UPDATE collect_identity_streams
            SET validation_status=%s, validation_detail=%s, updated_at=NOW(3)
            WHERE stream_id=%s
            """,
            (st, detail[:1000], sid),
        )

    try:
        from collect_01.display_store import sync_streams_for_task
        from report_04.phases import STREAM_IMAGE_STEP_KEY

        sync_streams_for_task(task_id, step_key=STREAM_IMAGE_STEP_KEY, stream_type="image")
        sync_streams_for_task(task_id, step_key="step5_streams", stream_type="image")
    except Exception as exc:
        logger.warning("图片模型比对后展示层双写失败: %s", exc)

    pass_n = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_identity_streams
        WHERE task_id=%s AND stream_type='image' AND validation_status='pass'
        """,
        (task_id,),
    )
    return {
        "ok": True,
        "source": "model_compare" if result.get("success") else "fallback",
        "conclusion": conclusion,
        "modelAnalysis": conclusion,
        "matched": int((pass_n or {}).get("c") or 0),
        "total": len(streams),
    }
