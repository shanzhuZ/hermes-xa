"""04 写报 4.3 社工库核验：URL 列表、命中落表、结论标记与覆盖度收口。

主路径：系统在 4.2 完成后自动调 ES（不依赖 Agent）。
Agent 仍可补调 search_country_wise；会话结束/超时有 fail-forward。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from collect_01 import db
from report_04.gates import can_advance_to_osint, get_step_status

logger = logging.getLogger(__name__)

OSINT_STEP_KEY = "step6_osint_es"
OSINT_CONCLUSION_MARKER = "[社工库核验结论]"
# 纯 HTTP 模块：禁止 import server.py（会拉 mcp/pydantic，Hook 脏 PYTHONPATH 下会炸）
_ES_HTTP_PATH = Path(__file__).resolve().parents[2] / "mcp" / "servers" / "es-search" / "es_http.py"
_es_http = None

# Hermes 会对 server 名中的连字符做 sanitize；同时兼容未 sanitize 形态
_OSINT_TOOL_SUFFIXES = (
    "search_country_wise",
    "list_es_indices",
    "es_cluster_health",
)


def is_osint_es_tool(tool_name: str) -> bool:
    n = (tool_name or "").strip()
    if not n:
        return False
    return any(n.endswith(suf) or n.endswith("__" + suf) for suf in _OSINT_TOOL_SUFFIXES)


def is_search_country_wise_tool(tool_name: str) -> bool:
    n = (tool_name or "").strip()
    return n.endswith("search_country_wise") or n.endswith("__search_country_wise")


def list_validated_profile_urls(task_id: str) -> List[Dict[str, str]]:
    """4.2 收敛后 verdict=validated 且有 profile_url 的账号。"""
    rows = db.fetch_all(
        """
        SELECT v.platform, v.account_id, p.profile_url, p.display_name
        FROM collect_validated_accounts v
        LEFT JOIN collect_profiles p
          ON p.task_id=v.task_id AND p.platform=v.platform AND p.account_id=v.account_id
        WHERE v.task_id=%s AND v.verdict='validated'
        """,
        (task_id,),
    )
    out: List[Dict[str, str]] = []
    seen = set()
    for row in rows or []:
        url = str((row or {}).get("profile_url") or "").strip()
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "platform": str((row or {}).get("platform") or ""),
                "account_id": str((row or {}).get("account_id") or ""),
                "profile_url": url,
                "display_name": str((row or {}).get("display_name") or ""),
            }
        )
    return out


def _guess_field_for_url(url: str) -> Optional[str]:
    host = (urlparse(url).netloc or "").lower()
    if "twitter.com" in host or "x.com" in host:
        return "twitter_url"
    if "facebook.com" in host or "fb.com" in host:
        return "facebook_url"
    if "linkedin.com" in host:
        return "linkedin_url"
    return None


def list_osint_query_targets(task_id: str) -> List[Dict[str, str]]:
    """country_wise 可精确查的 URL（仅 Twitter/Facebook/LinkedIn）。"""
    return [
        item
        for item in list_validated_profile_urls(task_id)
        if _guess_field_for_url(item.get("profile_url") or "")
    ]


def format_pending_urls_for_agent(task_id: str) -> List[str]:
    """待查 URL 行（供 build_agent_context）。"""
    pending = []
    queried = {
        str(r.get("profile_url") or "").strip().lower()
        for r in db.fetch_all(
            "SELECT profile_url FROM collect_osint_hits WHERE task_id=%s",
            (task_id,),
        )
        or []
        if r.get("profile_url")
    }
    queried_q = {
        str(r.get("query_text") or "").strip().lower()
        for r in db.fetch_all(
            "SELECT query_text FROM collect_osint_hits WHERE task_id=%s",
            (task_id,),
        )
        or []
        if r.get("query_text")
    }
    for item in list_osint_query_targets(task_id):
        url = item["profile_url"]
        if url.lower() in queried or url.lower() in queried_q:
            continue
        field = _guess_field_for_url(url)
        name = (item.get("display_name") or "").strip()
        name_hint = f" person_name={name}" if name else ""
        pending.append(
            f"- {item['platform']}/{item['account_id']}: {url} field={field}{name_hint}"
        )
    return pending


def _load_env_for_es() -> None:
    """加载仓库根/.env 与 es-search 旁 .env；清代理。须在 import es_http 之前调用。"""
    roots = (
        Path(__file__).resolve().parents[2] / ".env",
        _ES_HTTP_PATH.parent / ".env",
    )
    for root_env in roots:
        if not root_env.is_file():
            continue
        for line in root_env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)


def _load_es_http():
    """懒加载 es_http.py（纯 stdlib，不依赖 mcp/pydantic）。"""
    global _es_http
    if _es_http is not None:
        return _es_http
    import importlib.util
    import sys

    _load_env_for_es()
    if not _ES_HTTP_PATH.is_file():
        raise FileNotFoundError(f"es_http 不存在: {_ES_HTTP_PATH}")
    # 若曾被错误加载过带缓存的空模块，清掉
    sys.modules.pop("hermes_es_http", None)
    spec = importlib.util.spec_from_file_location("hermes_es_http", str(_ES_HTTP_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载 es_http")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _es_http = mod
    return mod


def _invoke_search_country_wise(
    query_text: str,
    *,
    field: Optional[str] = None,
    person_name: Optional[str] = None,
    size: int = 5,
) -> Dict[str, Any]:
    """同步 HTTP 调用 country_wise 精确检索，返回与 MCP 工具同形结果。"""
    mod = _load_es_http()
    data = mod.search_country_wise(
        query_text, field=field, person_name=person_name, size=size
    )
    data["source"] = "system_pipeline"
    return data


def _record_osint_row(
    task_id: str,
    *,
    platform: str,
    account_id: str,
    profile_url: str,
    query_text: str,
    data: Dict[str, Any],
) -> None:
    """落一条查询结果（含 0 命中/错误，保证覆盖度可收口）。"""
    source_index = str(data.get("index") or "dw_identity_info_lk_country_wise_new_all")[:128]
    results = data.get("results") if isinstance(data.get("results"), list) else []
    if data.get("error"):
        hit_count = 0
    else:
        hit_count = int(data.get("total_hits") or data.get("returned") or len(results) or 0)
    db.execute(
        """
        INSERT INTO collect_osint_hits
          (task_id, platform, account_id, profile_url, source_index, query_text,
           hit_count, hit_json, tool_output_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL)
        ON DUPLICATE KEY UPDATE
          hit_count=VALUES(hit_count),
          hit_json=VALUES(hit_json),
          platform=VALUES(platform),
          account_id=VALUES(account_id),
          profile_url=VALUES(profile_url),
          updated_at=NOW(3)
        """,
        (
            task_id,
            platform or None,
            account_id or None,
            (profile_url or "")[:2000] or None,
            source_index or "",
            (query_text or "")[:1024] or "",
            hit_count,
            db.json_dumps(data),
        ),
    )
    try:
        from collect_01.display_store import sync_osint_hit_display

        sync_osint_hit_display(
            {
                "task_id": task_id,
                "platform": platform,
                "account_id": account_id,
                "profile_url": profile_url,
                "source_index": source_index,
                "query_text": query_text,
                "hit_count": hit_count,
            }
        )
    except Exception as exc:
        logger.warning("社工库展示双写失败 task=%s: %s", task_id, exc)


def run_osint_es_pipeline(store: Any, task_id: str) -> bool:
    """系统自动跑 4.3：逐 URL 查 ES → 落表 → 按命中收口。不依赖 Agent。"""
    store.ensure_step_row(task_id, OSINT_STEP_KEY)
    if not can_advance_to_osint(task_id).get("ok"):
        return False
    cur = get_step_status(task_id, OSINT_STEP_KEY)
    if cur in {"completed", "skipped"}:
        return False
    targets = list_osint_query_targets(task_id)
    if not targets:
        store.set_step_status(
            task_id,
            OSINT_STEP_KEY,
            "skipped",
            message="无可查 Twitter/Facebook/LinkedIn profile_url，跳过社工库核验",
        )
        return True

    store.set_step_status(
        task_id,
        OSINT_STEP_KEY,
        "running",
        message=f"系统社工库核验中（{len(targets)} 个 URL）",
    )
    try:
        from report_04.thought_progress import emit_system_thinking

        emit_system_thinking(
            task_id,
            f"【系统·步骤4.3】开始社工库核验，共 {len(targets)} 个 profile URL。",
        )
    except Exception:
        pass
    t0 = time.perf_counter()
    ok_n = 0
    err_n = 0
    for item in targets:
        url = item["profile_url"]
        field = _guess_field_for_url(url)
        person_name = (item.get("display_name") or "").strip() or None
        try:
            data = _invoke_search_country_wise(
                url, field=field, person_name=person_name, size=5
            )
            _record_osint_row(
                task_id,
                platform=item.get("platform") or "",
                account_id=item.get("account_id") or "",
                profile_url=url,
                query_text=url,
                data=data,
            )
            ok_n += 1
        except Exception as exc:
            err_n += 1
            logger.warning("系统社工库查询失败 task=%s url=%s: %s", task_id, url[:80], exc)
            _record_osint_row(
                task_id,
                platform=item.get("platform") or "",
                account_id=item.get("account_id") or "",
                profile_url=url,
                query_text=url,
                data={
                    "error": str(exc)[:500],
                    "query": url,
                    "index": "dw_identity_info_lk_country_wise_new_all",
                    "total_hits": 0,
                    "returned": 0,
                    "results": [],
                    "source": "system_pipeline",
                },
            )

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    has_hits = _has_positive_hits(task_id)
    if has_hits:
        store.set_step_status(
            task_id,
            OSINT_STEP_KEY,
            "completed",
            message=f"系统社工库核验完成：有命中（查{ok_n}失败{err_n}，{elapsed_ms}ms）",
            payload={
                "source": "system_pipeline",
                "hasHits": True,
                "queried": ok_n,
                "errors": err_n,
                "elapsedMs": elapsed_ms,
            },
        )
    else:
        store.set_step_status(
            task_id,
            OSINT_STEP_KEY,
            "skipped",
            message=f"系统社工库核验完成：无命中（查{ok_n}失败{err_n}，{elapsed_ms}ms）",
            payload={
                "source": "system_pipeline",
                "hasHits": False,
                "queried": ok_n,
                "errors": err_n,
                "elapsedMs": elapsed_ms,
            },
        )
    logger.info(
        "run_osint_es_pipeline task=%s ok=%s err=%s hits=%s %sms",
        task_id,
        ok_n,
        err_n,
        has_hits,
        elapsed_ms,
    )
    try:
        from report_04.thought_progress import emit_system_thinking

        emit_system_thinking(
            task_id,
            f"【系统·步骤4.3】社工库核验结束：命中={'有' if has_hits else '无'}，"
            f"成功查询 {ok_n}，失败 {err_n}。请立刻调发文工具，禁止结束会话。",
        )
    except Exception:
        pass
    try:
        from report_04.session_continue import maybe_continue_agent_session

        maybe_continue_agent_session(
            store, task_id, reason="osint_done", kind="posts"
        )
    except Exception as exc:
        logger.warning("osint 后续跑失败 task=%s: %s", task_id, exc)
    return True


def kickoff_osint_if_ready(store: Any, task_id: str) -> None:
    """4.1+4.2 完成后自动跑 4.3 系统管线（不依赖 Agent）。"""
    store.ensure_step_row(task_id, OSINT_STEP_KEY)
    if not can_advance_to_osint(task_id).get("ok"):
        return
    cur = get_step_status(task_id, OSINT_STEP_KEY)
    if cur in {"completed", "skipped"}:
        return
    try:
        run_osint_es_pipeline(store, task_id)
    except Exception as exc:
        logger.exception("kickoff 系统社工库失败 task=%s: %s", task_id, exc)
        # 失败也不永久 running：记 skipped，避免卡死
        if get_step_status(task_id, OSINT_STEP_KEY) not in {"completed", "skipped"}:
            store.set_step_status(
                task_id,
                OSINT_STEP_KEY,
                "skipped",
                message=f"系统社工库核验失败已跳过：{str(exc)[:120]}",
            )


def _parse_tool_json(tool_output: str) -> Optional[Dict[str, Any]]:
    text = (tool_output or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    # 兼容夹杂前后缀的 JSON
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _match_account(task_id: str, query_text: str) -> Tuple[str, str, str]:
    q = (query_text or "").strip()
    if not q:
        return "", "", ""
    for item in list_validated_profile_urls(task_id):
        if item["profile_url"].lower() == q.lower() or q.lower() in item["profile_url"].lower():
            return item["platform"], item["account_id"], item["profile_url"]
    return "", "", q


def upsert_osint_hit_from_tool(
    task_id: str,
    *,
    tool_args: Optional[Dict[str, Any]],
    tool_output: str,
    tool_output_id: Optional[int] = None,
) -> bool:
    """解析 search_country_wise 成功结果并落薄表 + 展示层。"""
    args = tool_args or {}
    query_text = str(args.get("query_text") or args.get("query") or "").strip()
    data = _parse_tool_json(tool_output)
    if data is None:
        return False
    if data.get("error"):
        logger.info("社工库查询报错 task=%s: %s", task_id, data.get("error"))
        return False
    if not query_text:
        query_text = str(data.get("query") or "").strip()
    platform, account_id, profile_url = _match_account(task_id, query_text)
    if not profile_url:
        profile_url = query_text
    source_index = str(data.get("index") or "")[:128]
    results = data.get("results")
    if not isinstance(results, list):
        results = []
    hit_count = int(data.get("total_hits") or data.get("returned") or len(results) or 0)
    hit_json = db.json_dumps(data)
    # 唯一键含 source_index：空串代替 NULL，避免重复插入
    idx_key = source_index or ""
    q_key = (query_text or "")[:1024] or ""
    db.execute(
        """
        INSERT INTO collect_osint_hits
          (task_id, platform, account_id, profile_url, source_index, query_text,
           hit_count, hit_json, tool_output_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          hit_count=VALUES(hit_count),
          hit_json=VALUES(hit_json),
          tool_output_id=VALUES(tool_output_id),
          platform=VALUES(platform),
          account_id=VALUES(account_id),
          profile_url=VALUES(profile_url),
          updated_at=NOW(3)
        """,
        (
            task_id,
            platform or None,
            account_id or None,
            (profile_url or "")[:2000] or None,
            idx_key,
            q_key,
            hit_count,
            hit_json,
            tool_output_id,
        ),
    )
    try:
        from collect_01.display_store import sync_osint_hit_display

        sync_osint_hit_display(
            {
                "task_id": task_id,
                "platform": platform,
                "account_id": account_id,
                "profile_url": profile_url,
                "source_index": source_index,
                "query_text": query_text,
                "hit_count": hit_count,
            }
        )
    except Exception as exc:
        logger.warning("社工库展示双写失败 task=%s: %s", task_id, exc)
    return True


def parse_osint_conclusion(assistant: str) -> Optional[str]:
    text = (assistant or "").strip()
    if not text:
        return None
    idx = text.find(OSINT_CONCLUSION_MARKER)
    if idx < 0:
        return None
    body = text[idx + len(OSINT_CONCLUSION_MARKER) :].strip()
    if not body:
        body = text[idx:].strip()
    return body[:20000] if body else None


def _has_positive_hits(task_id: str) -> bool:
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM collect_osint_hits
        WHERE task_id=%s AND hit_count > 0
        """,
        (task_id,),
    )
    return int((row or {}).get("c") or 0) > 0


def apply_osint_conclusion_from_assistant(store: Any, task_id: str, assistant: str) -> bool:
    """解析 [社工库核验结论] → 有命中 completed / 无命中 skipped。"""
    conclusion = parse_osint_conclusion(assistant)
    if not conclusion:
        return False
    store.ensure_step_row(task_id, OSINT_STEP_KEY)
    cur = get_step_status(task_id, OSINT_STEP_KEY)
    if cur in {"completed", "skipped"}:
        store.set_step_status(
            task_id,
            OSINT_STEP_KEY,
            cur,
            message="社工库核验结论已更新",
            payload={"conclusion": conclusion, "source": "agent_marker"},
            touch_updated_at=False,
        )
        return True
    # 以库内命中为准；Agent 声明无命中且库内无命中 → skipped
    has_hits = _has_positive_hits(task_id)
    if has_hits:
        status, msg = "completed", "社工库核验完成（有命中）"
    else:
        status, msg = "skipped", "社工库无命中，已跳过"
    store.set_step_status(
        task_id,
        OSINT_STEP_KEY,
        status,
        message=msg,
        payload={"conclusion": conclusion, "source": "agent_marker", "hasHits": has_hits},
    )
    return True


def coverage_complete(task_id: str) -> bool:
    """仅统计 Twitter/FB/LinkedIn URL 是否都已有查询记录。"""
    urls = list_osint_query_targets(task_id)
    if not urls:
        return True
    queried = {
        str(r.get("profile_url") or "").strip().lower()
        for r in db.fetch_all(
            "SELECT profile_url FROM collect_osint_hits WHERE task_id=%s",
            (task_id,),
        )
        or []
        if r.get("profile_url")
    }
    queried_q = {
        str(r.get("query_text") or "").strip().lower()
        for r in db.fetch_all(
            "SELECT query_text FROM collect_osint_hits WHERE task_id=%s",
            (task_id,),
        )
        or []
        if r.get("query_text")
    }
    for item in urls:
        u = item["profile_url"].lower()
        if u not in queried and u not in queried_q:
            return False
    return True


def _count_osint_tool_calls(task_id: str) -> int:
    row = db.fetch_one(
        """
        SELECT COUNT(*) AS c FROM hermes_tool_outputs
        WHERE task_id=%s AND status='success'
          AND (tool_name LIKE '%%search_country_wise'
               OR tool_name LIKE '%%es_search%%'
               OR tool_name LIKE '%%es-search%%'
               OR phase='step6_osint_es')
        """,
        (task_id,),
    )
    return int((row or {}).get("c") or 0)


def _osint_running_seconds(task_id: str) -> Optional[float]:
    from datetime import datetime

    row = db.fetch_one(
        """
        SELECT updated_at FROM collect_phase_steps
        WHERE task_id=%s AND step_key=%s
        """,
        (task_id, OSINT_STEP_KEY),
    )
    ts = (row or {}).get("updated_at")
    if ts is None:
        return None
    if hasattr(ts, "timestamp"):
        try:
            return max(0.0, (datetime.now() - ts).total_seconds())
        except Exception:
            return None
    return None


def _close_osint_step(
    store: Any,
    task_id: str,
    *,
    reason: str,
    force_skip: bool = False,
) -> bool:
    if force_skip or not _has_positive_hits(task_id):
        status, msg = "skipped", reason
        has_hits = False
    else:
        status, msg = "completed", reason
        has_hits = True
    store.set_step_status(
        task_id,
        OSINT_STEP_KEY,
        status,
        message=msg[:200],
        payload={"source": "fail_forward", "hasHits": has_hits},
    )
    return True


def maybe_close_osint_by_coverage(store: Any, task_id: str, *, reason: str = "覆盖度兜底") -> bool:
    """URL 都已查询过则按命中收口。"""
    store.ensure_step_row(task_id, OSINT_STEP_KEY)
    cur = get_step_status(task_id, OSINT_STEP_KEY)
    if cur in {"completed", "skipped"}:
        return False
    if not can_advance_to_osint(task_id).get("ok"):
        return False
    urls = list_osint_query_targets(task_id)
    if not urls:
        return _close_osint_step(
            store, task_id, reason=f"{reason}：无可查 Twitter/Facebook/LinkedIn URL", force_skip=True
        )
    if not coverage_complete(task_id):
        return False
    if _has_positive_hits(task_id):
        return _close_osint_step(
            store, task_id, reason=f"{reason}：URL 已查完且有命中", force_skip=False
        )
    return _close_osint_step(
        store, task_id, reason=f"{reason}：URL 已查完且无命中", force_skip=True
    )


def close_osint_on_session_end(store: Any, task_id: str) -> bool:
    """会话结束：有覆盖度按命中收口；未查询也必须收口，禁止 4.3 永久 running。"""
    store.ensure_step_row(task_id, OSINT_STEP_KEY)
    cur = get_step_status(task_id, OSINT_STEP_KEY)
    if cur in {"completed", "skipped"}:
        return False
    if maybe_close_osint_by_coverage(store, task_id, reason="会话结束覆盖度兜底"):
        return True
    n_tools = _count_osint_tool_calls(task_id)
    if n_tools <= 0:
        return _close_osint_step(
            store,
            task_id,
            reason="会话结束：Agent 未调用 search_country_wise，跳过社工库核验",
            force_skip=True,
        )
    # 查过一部分但未覆盖全：仍 skip，避免卡死
    return _close_osint_step(
        store,
        task_id,
        reason=f"会话结束：社工库未查全（工具成功 {n_tools} 次），按无命中跳过",
        force_skip=True,
    )


def maybe_fail_forward_stale_osint(
    store: Any,
    task_id: str,
    *,
    min_wait_seconds: float = 120.0,
) -> bool:
    """4.3 running 过久且无 ES 工具调用 → skipped，避免卡死。"""
    store.ensure_step_row(task_id, OSINT_STEP_KEY)
    cur = get_step_status(task_id, OSINT_STEP_KEY)
    if cur not in {"running", "pending"}:
        return False
    if not can_advance_to_osint(task_id).get("ok"):
        return False
    age = _osint_running_seconds(task_id)
    if age is None or age < min_wait_seconds:
        return False
    if maybe_close_osint_by_coverage(store, task_id, reason="超时覆盖度兜底"):
        return True
    if _count_osint_tool_calls(task_id) > 0:
        return False
    logger.warning(
        "4.3 空转 fail-forward task=%s age=%.0fs tools=0", task_id, age
    )
    return _close_osint_step(
        store,
        task_id,
        reason=f"超时 {int(age)}s：未调用社工库工具，跳过 4.3",
        force_skip=True,
    )


def format_osint_hits_for_report(task_id: str, *, limit: int = 12) -> str:
    """终稿上下文：命中摘要（无命中返回空串）。"""
    rows = db.fetch_all(
        """
        SELECT platform, account_id, profile_url, source_index, hit_count, hit_json
        FROM collect_osint_hits
        WHERE task_id=%s AND hit_count > 0
        ORDER BY id ASC
        LIMIT %s
        """,
        (task_id, limit),
    )
    if not rows:
        return ""
    lines = ["【社工库命中摘要】请揉进「一、账号基本信息」或「四、核查思路」，勿单开「社工库」专节："]
    for row in rows:
        plat = row.get("platform") or "?"
        aid = row.get("account_id") or "?"
        url = row.get("profile_url") or ""
        cnt = row.get("hit_count") or 0
        snippet = ""
        try:
            payload = json.loads(row.get("hit_json") or "{}")
            results = payload.get("results") or []
            if results and isinstance(results[0], dict):
                src = results[0]
                name = src.get("full_name") or src.get("name") or ""
                snippet = str(name)[:80]
        except Exception:
            pass
        lines.append(f"- {plat}/{aid} url={url} hits={cnt}" + (f" 样例={snippet}" if snippet else ""))
    return "\n".join(lines)
