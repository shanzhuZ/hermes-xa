# -*- coding: utf-8 -*-
"""04 写报 4.7 全球人员信息检索（PDL）。

主路径：4.2 认定完成后与 4.3 社工库并行自动查 PDL（不依赖 4.3 成败）；
无命中/失败 → skipped，不挡发文。
Agent 可在 4.2 完成后补调 mcp_pdl_person_search_person。
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlparse

import requests
import urllib3

from collect_01 import db
from report_04.gates import get_step_status

logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PDL_STEP_KEY = "step6_pdl"
PDL_BASE_URL = "https://api.peopledatalabs.com/v5/person/search"
PDL_MAX_TARGETS = 5  # 单任务最多查几个 validated URL，控成本


def is_pdl_tool(tool_name: str) -> bool:
    n = (tool_name or "").strip()
    if not n:
        return False
    return (
        n.endswith("search_person")
        or "pdl_person" in n
        or n.startswith("mcp_pdl_")
    )


def can_advance_to_pdl(task_id: str) -> Dict[str, Any]:
    """4.1+4.2 完成后开放 4.7（与 4.3 并行，不依赖社工库成败）。"""
    if get_step_status(task_id, "step5_streams") != "completed":
        return {"ok": False, "message": "step5_streams 未完成"}
    if get_step_status(task_id, "step6_validated") != "completed":
        return {"ok": False, "message": "step6_validated 未完成"}
    return {"ok": True, "message": "满足 PDL 推进条件"}


def _load_pdl_env() -> None:
    root_env = Path(__file__).resolve().parents[2] / ".env"
    if not root_env.is_file():
        return
    for line in root_env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def _pdl_api_key() -> str:
    _load_pdl_env()
    return (os.environ.get("PDL_API_KEY") or "").strip()


def _pdl_proxy() -> str:
    return (
        os.environ.get("PDL_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or ""
    ).strip()


def _normalize_social_link(social_link: str) -> str:
    link = (social_link or "").strip()
    if not link:
        return ""
    if link.startswith("http"):
        return (
            link.replace("https://www.", "")
            .replace("http://www.", "")
            .replace(" ", "")
            .replace("http://", "")
            .replace("https://", "")
        )
    if link.startswith("www"):
        return link.replace("www.", "").replace(" ", "")
    return link


def _guess_pdl_ok_url(url: str) -> bool:
    """PDL profiles.url 较稳的平台：Twitter/X、Facebook、LinkedIn。"""
    host = (urlparse(url).netloc or "").lower()
    return any(
        h in host
        for h in (
            "twitter.com",
            "x.com",
            "facebook.com",
            "fb.com",
            "linkedin.com",
        )
    )


def list_pdl_query_targets(task_id: str) -> List[Dict[str, str]]:
    """validated 账号中可用于 PDL social_link 的 profile_url。"""
    from report_04.osint_es import list_validated_profile_urls

    out: List[Dict[str, str]] = []
    for item in list_validated_profile_urls(task_id):
        url = item.get("profile_url") or ""
        if not _guess_pdl_ok_url(url):
            continue
        out.append(item)
        if len(out) >= PDL_MAX_TARGETS:
            break
    return out


def _build_social_query(social_link: str) -> dict:
    link = _normalize_social_link(social_link)
    return {
        "query": {
            "bool": {
                "must": [{"match_phrase": {"profiles.url": link}}],
            }
        }
    }


def _call_pdl_search(social_link: str, *, size: int = 1) -> Dict[str, Any]:
    """同步调 PDL Person Search（与 MCP 查询逻辑一致）。"""
    api_key = _pdl_api_key()
    if not api_key:
        return {"success": False, "error": "未配置 PDL_API_KEY"}
    query = _build_social_query(social_link)
    url = (
        f"{PDL_BASE_URL}"
        f"?query={quote(json.dumps(query, ensure_ascii=False), safe='')}"
        f"&size={max(1, min(int(size), 5))}&titlecase=false&pretty=false&dataset=all"
    )
    headers = {"Content-Type": "application/json", "X-API-Key": api_key}
    proxies = None
    proxy = _pdl_proxy()
    if proxy:
        proxies = {"http": proxy, "https": proxy}
    timeout = int(os.environ.get("PDL_TIMEOUT") or "60")
    try:
        resp = requests.get(
            url, headers=headers, proxies=proxies, timeout=timeout, verify=False
        )
    except requests.RequestException as exc:
        return {"success": False, "error": str(exc)[:500], "query": query}
    try:
        body = resp.json()
    except Exception:
        body = {"raw": (resp.text or "")[:2000]}
    if resp.status_code != 200:
        err = body.get("error") if isinstance(body, dict) else body
        return {
            "success": False,
            "status_code": resp.status_code,
            "error": err,
            "query": query,
        }
    data = body.get("data") if isinstance(body, dict) else None
    return {
        "success": True,
        "status_code": resp.status_code,
        "total": body.get("total") if isinstance(body, dict) else None,
        "returned": len(data) if isinstance(data, list) else 0,
        "data": data if data is not None else body,
        "query": query,
        "source": "system_pipeline",
        "social_link": social_link,
    }


def _pdl_text(value: Any) -> str:
    """PDL 低权限字段常返回 True 表示「有值但未开放」；过滤布尔，只保留真实文本。"""
    if value is None or isinstance(value, bool):
        return ""
    text = str(value).strip()
    return text


def _pdl_title_name(title: Any) -> str:
    if isinstance(title, dict):
        return _pdl_text(title.get("name"))
    return _pdl_text(title)


def _summarize_person(person: Dict[str, Any]) -> Dict[str, Any]:
    """截取展示用字段（保留学历/经历/社交等；整包过大不入库）。"""
    if not isinstance(person, dict):
        return {}
    emails = person.get("emails") if isinstance(person.get("emails"), list) else []
    phones = person.get("phone_numbers") if isinstance(person.get("phone_numbers"), list) else []
    profiles = person.get("profiles") if isinstance(person.get("profiles"), list) else []
    education = person.get("education") if isinstance(person.get("education"), list) else []
    experience = person.get("experience") if isinstance(person.get("experience"), list) else []
    skills = person.get("skills") if isinstance(person.get("skills"), list) else []

    edu_lines: List[str] = []
    for e in education[:5]:
        if not isinstance(e, dict):
            continue
        school = e.get("school")
        school_name = (
            _pdl_text(school.get("name")) if isinstance(school, dict) else _pdl_text(school)
        )
        degrees = e.get("degrees") if isinstance(e.get("degrees"), list) else []
        majors = e.get("majors") if isinstance(e.get("majors"), list) else []
        deg = "、".join(_pdl_text(d) for d in degrees[:3] if _pdl_text(d))
        maj = "、".join(_pdl_text(m) for m in majors[:3] if _pdl_text(m))
        parts = [p for p in (school_name, deg, maj) if p]
        if parts:
            edu_lines.append(" / ".join(parts))

    exp_lines: List[str] = []
    for e in experience[:6]:
        if not isinstance(e, dict):
            continue
        company = e.get("company")
        co = (
            _pdl_text(company.get("name")) if isinstance(company, dict) else _pdl_text(company)
        )
        title = _pdl_title_name(e.get("title_name") or e.get("title"))
        start = _pdl_text(e.get("start_date"))
        end = _pdl_text(e.get("end_date")) or "至今"
        period = f"{start}-{end}" if start else ""
        bits = [b for b in (co, title, period) if b]
        if bits:
            exp_lines.append(" | ".join(bits))

    profile_lines: List[str] = []
    for pr in profiles[:8]:
        if not isinstance(pr, dict):
            continue
        net = _pdl_text(pr.get("network"))
        url = _pdl_text(pr.get("url"))
        if net and url:
            profile_lines.append(f"{net}: {url}")
        elif url:
            profile_lines.append(url)

    email_samples = [
        (e.get("address") if isinstance(e, dict) else str(e))
        for e in emails[:5]
        if not isinstance(e, bool)
    ]
    email_samples = [s for s in (_pdl_text(x) for x in email_samples) if s]
    phone_samples = [_pdl_text(p) for p in phones[:5] if _pdl_text(p)]

    # 布尔 True = 有值但套餐未开放明细
    gated: List[str] = []
    for label, key in (
        ("邮箱", "emails"),
        ("电话", "phone_numbers"),
        ("地区明细", "location_name"),
        ("出生年份", "birth_year"),
    ):
        if person.get(key) is True:
            gated.append(label)

    return {
        "full_name": _pdl_text(person.get("full_name") or person.get("name")),
        "first_name": _pdl_text(person.get("first_name")),
        "last_name": _pdl_text(person.get("last_name")),
        "gender": _pdl_text(person.get("gender")),
        "birth_year": _pdl_text(person.get("birth_year")),
        "industry": _pdl_text(person.get("industry")),
        "job_title": _pdl_text(person.get("job_title")),
        "job_company_name": _pdl_text(person.get("job_company_name")),
        "job_company_website": _pdl_text(person.get("job_company_website")),
        "job_company_size": _pdl_text(person.get("job_company_size")),
        "job_company_industry": _pdl_text(person.get("job_company_industry")),
        "location_name": _pdl_text(person.get("location_name")),
        "location_locality": _pdl_text(person.get("location_locality")),
        "location_region": _pdl_text(person.get("location_region")),
        "location_country": _pdl_text(person.get("location_country")),
        "linkedin_url": _pdl_text(person.get("linkedin_url")),
        "twitter_url": _pdl_text(person.get("twitter_url")),
        "facebook_url": _pdl_text(person.get("facebook_url")),
        "github_url": _pdl_text(person.get("github_url")),
        "education": "；".join(edu_lines),
        "experience": "；".join(exp_lines),
        "profiles": "；".join(profile_lines),
        "skills": "、".join(_pdl_text(s) for s in skills[:12] if _pdl_text(s)),
        "email_count": len(emails) if isinstance(person.get("emails"), list) else (1 if person.get("emails") is True else 0),
        "phone_count": len(phones) if isinstance(person.get("phone_numbers"), list) else (1 if person.get("phone_numbers") is True else 0),
        "profile_count": len(profiles),
        "emails_sample": email_samples,
        "phones_sample": phone_samples,
        "emails": "、".join(email_samples),
        "phones": "、".join(phone_samples),
        "gated_fields": "、".join(gated),
    }


def _sync_pdl_hit_to_display(task_id: str, hit: Dict[str, Any]) -> None:
    """命中写入 collect_display_records，供 /steps/.../data 查询。"""
    if not isinstance(hit, dict):
        return
    summary = hit.get("summary") if isinstance(hit.get("summary"), dict) else {}
    try:
        from collect_01.display_store import sync_pdl_hit_display

        emails = summary.get("emails") or ""
        if not emails and isinstance(summary.get("emails_sample"), list):
            emails = "、".join(str(x) for x in summary.get("emails_sample") if x)
        phones = summary.get("phones") or ""
        if not phones and isinstance(summary.get("phones_sample"), list):
            phones = "、".join(str(x) for x in summary.get("phones_sample") if x)
        sync_pdl_hit_display(
            {
                "task_id": task_id,
                "platform": hit.get("platform") or "",
                "account_id": hit.get("account_id") or "",
                "profile_url": hit.get("profile_url") or "",
                "full_name": summary.get("full_name") or "",
                "industry": summary.get("industry") or "",
                "job_title": summary.get("job_title") or "",
                "job_company_name": summary.get("job_company_name") or "",
                "job_company_website": summary.get("job_company_website") or "",
                "job_company_size": summary.get("job_company_size") or "",
                "location_country": summary.get("location_country") or "",
                "location_name": summary.get("location_name") or "",
                "linkedin_url": summary.get("linkedin_url") or "",
                "twitter_url": summary.get("twitter_url") or "",
                "facebook_url": summary.get("facebook_url") or "",
                "github_url": summary.get("github_url") or "",
                "education": summary.get("education") or "",
                "experience": summary.get("experience") or "",
                "profiles": summary.get("profiles") or "",
                "skills": summary.get("skills") or "",
                "emails": emails,
                "phones": phones,
                "gated_fields": summary.get("gated_fields") or "",
                "hit_count": hit.get("total") if hit.get("total") is not None else 1,
            }
        )
    except Exception as exc:
        logger.warning("PDL 展示层写入失败 task=%s: %s", task_id, exc)


def sync_pdl_hits_from_payload(task_id: str) -> int:
    """从 step6_pdl.payload_json 回填展示层（历史任务补数据用）。"""
    row = db.fetch_one(
        "SELECT payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, PDL_STEP_KEY),
    ) or {}
    raw = row.get("payload_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return 0
    hits = raw.get("hits")
    if not isinstance(hits, list):
        return 0
    n = 0
    for h in hits:
        if not isinstance(h, dict):
            continue
        _sync_pdl_hit_to_display(task_id, h)
        n += 1
    return n


def format_pdl_hits_for_report(task_id: str) -> List[str]:
    """终稿可用的简短命中摘要（从 step payload 读）。"""
    row = db.fetch_one(
        "SELECT payload_json FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, PDL_STEP_KEY),
    ) or {}
    raw = row.get("payload_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        return []
    hits = raw.get("hits")
    if not isinstance(hits, list):
        return []
    lines: List[str] = []
    for h in hits[:8]:
        if not isinstance(h, dict):
            continue
        s = h.get("summary") if isinstance(h.get("summary"), dict) else {}
        name = s.get("full_name") or ""
        company = s.get("job_company_name") or ""
        title = s.get("job_title") or ""
        plat = h.get("platform") or ""
        aid = h.get("account_id") or ""
        if title and company:
            lines.append(f"- {plat}/{aid}: {name}（{title} @ {company}）")
        elif title:
            lines.append(f"- {plat}/{aid}: {name}（{title}）")
        elif company:
            lines.append(f"- {plat}/{aid}: {name}（{company}）")
        else:
            lines.append(f"- {plat}/{aid}: {name}")
    return lines


def run_pdl_pipeline(store: Any, task_id: str) -> bool:
    """系统自动跑 4.7：逐 URL 查 PDL → 有命中 completed / 无命中 skipped。"""
    store.ensure_step_row(task_id, PDL_STEP_KEY)
    if not can_advance_to_pdl(task_id).get("ok"):
        return False
    cur = get_step_status(task_id, PDL_STEP_KEY)
    if cur in {"completed", "skipped"}:
        return False

    if not _pdl_api_key():
        store.set_step_status(
            task_id,
            PDL_STEP_KEY,
            "skipped",
            message="未配置 PDL_API_KEY，跳过全球人员信息检索",
            payload={"source": "system_pipeline", "hasHits": False, "reason": "no_api_key"},
        )
        _after_pdl_done(store, task_id)
        return True

    targets = list_pdl_query_targets(task_id)
    if not targets:
        store.set_step_status(
            task_id,
            PDL_STEP_KEY,
            "skipped",
            message="无可查 Twitter/Facebook/LinkedIn profile_url，跳过 PDL",
            payload={"source": "system_pipeline", "hasHits": False, "reason": "no_targets"},
        )
        _after_pdl_done(store, task_id)
        return True

    store.set_step_status(
        task_id,
        PDL_STEP_KEY,
        "running",
        message=f"全球人员信息检索中（{len(targets)} 个 URL）",
    )
    try:
        from report_04.thought_progress import emit_system_thinking

        emit_system_thinking(
            task_id,
            f"【系统·步骤4.7】开始全球人员信息检索（PDL），共 {len(targets)} 个 profile URL。",
        )
    except Exception:
        pass

    t0 = time.perf_counter()
    hits: List[Dict[str, Any]] = []
    ok_n = 0
    err_n = 0
    for item in targets:
        url = item["profile_url"]
        try:
            data = _call_pdl_search(url, size=1)
            ok_n += 1
            persons = data.get("data") if isinstance(data.get("data"), list) else []
            if data.get("success") and persons:
                person = persons[0] if isinstance(persons[0], dict) else {}
                hit = {
                    "platform": item.get("platform") or "",
                    "account_id": item.get("account_id") or "",
                    "profile_url": url,
                    "summary": _summarize_person(person),
                    "total": data.get("total"),
                }
                hits.append(hit)
                _sync_pdl_hit_to_display(task_id, hit)
            elif not data.get("success"):
                err_n += 1
                logger.warning(
                    "PDL 查询失败 task=%s url=%s: %s",
                    task_id,
                    url[:80],
                    str(data.get("error"))[:160],
                )
        except Exception as exc:
            err_n += 1
            logger.warning("PDL 查询异常 task=%s url=%s: %s", task_id, url[:80], exc)

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    payload = {
        "source": "system_pipeline",
        "hasHits": bool(hits),
        "queried": ok_n,
        "errors": err_n,
        "elapsedMs": elapsed_ms,
        "hits": hits,
    }
    if hits:
        store.set_step_status(
            task_id,
            PDL_STEP_KEY,
            "completed",
            message=f"全球人员信息检索完成：命中 {len(hits)}（查{ok_n}失败{err_n}，{elapsed_ms}ms）",
            payload=payload,
        )
    else:
        store.set_step_status(
            task_id,
            PDL_STEP_KEY,
            "skipped",
            message=f"全球人员信息检索完成：无命中（查{ok_n}失败{err_n}，{elapsed_ms}ms）",
            payload=payload,
        )
    logger.info(
        "run_pdl_pipeline task=%s hits=%s ok=%s err=%s %sms",
        task_id,
        len(hits),
        ok_n,
        err_n,
        elapsed_ms,
    )
    try:
        from report_04.thought_progress import emit_system_thinking

        emit_system_thinking(
            task_id,
            f"【系统·步骤4.7】PDL 结束：命中={'有' if hits else '无'}。"
            "请立刻调发文工具，禁止结束会话。",
        )
    except Exception:
        pass
    _after_pdl_done(store, task_id)
    return True


def _after_pdl_done(store: Any, task_id: str) -> None:
    """PDL 终态后催发文续跑。"""
    try:
        from report_04.session_continue import maybe_continue_agent_session

        maybe_continue_agent_session(store, task_id, reason="pdl_done", kind="posts")
    except Exception as exc:
        logger.warning("pdl 后续跑失败 task=%s: %s", task_id, exc)


def kickoff_pdl_if_ready(store: Any, task_id: str) -> None:
    """4.2 完成后自动跑 4.7（与 4.3 并行，不依赖社工库）。"""
    store.ensure_step_row(task_id, PDL_STEP_KEY)
    if not can_advance_to_pdl(task_id).get("ok"):
        return
    cur = get_step_status(task_id, PDL_STEP_KEY)
    if cur in {"completed", "skipped"}:
        return
    try:
        run_pdl_pipeline(store, task_id)
    except Exception as exc:
        logger.exception("kickoff PDL 失败 task=%s: %s", task_id, exc)
        if get_step_status(task_id, PDL_STEP_KEY) not in {"completed", "skipped"}:
            store.set_step_status(
                task_id,
                PDL_STEP_KEY,
                "skipped",
                message=f"全球人员信息检索失败已跳过：{str(exc)[:120]}",
                payload={"source": "system_pipeline", "hasHits": False, "error": str(exc)[:200]},
            )
            _after_pdl_done(store, task_id)


def spawn_detached_pdl(task_id: str) -> bool:
    try:
        from report_04.session_continue import spawn_detached_python_module

        return spawn_detached_python_module(
            "report_04.pdl_worker",
            ["--task-id", str(task_id)],
        )
    except Exception as exc:
        logger.warning("spawn_detached_pdl 失败 task=%s: %s", task_id, exc)
        return False


def close_pdl_on_session_end(store: Any, task_id: str) -> bool:
    """会话结束：未完成则 skip，禁止永久 running。"""
    store.ensure_step_row(task_id, PDL_STEP_KEY)
    cur = get_step_status(task_id, PDL_STEP_KEY)
    if cur in {"completed", "skipped"}:
        return False
    if not can_advance_to_pdl(task_id).get("ok"):
        logger.info("会话结束跳过 4.7 收口 task=%s：尚未到 PDL 阶段", task_id)
        return False
    store.set_step_status(
        task_id,
        PDL_STEP_KEY,
        "skipped",
        message="会话结束：全球人员信息检索未完成，已跳过",
        payload={"source": "session_end", "hasHits": False},
    )
    return True


def maybe_fail_forward_stale_pdl(
    store: Any,
    task_id: str,
    *,
    min_wait_seconds: int = 180,
) -> bool:
    """running 过久则 skip，避免卡发文。"""
    from datetime import datetime

    store.ensure_step_row(task_id, PDL_STEP_KEY)
    cur = get_step_status(task_id, PDL_STEP_KEY)
    if cur != "running":
        return False
    if not can_advance_to_pdl(task_id).get("ok"):
        return False
    row = db.fetch_one(
        "SELECT updated_at FROM collect_phase_steps WHERE task_id=%s AND step_key=%s",
        (task_id, PDL_STEP_KEY),
    )
    ts = (row or {}).get("updated_at")
    if ts is None or not hasattr(ts, "timestamp"):
        return False
    try:
        age = max(0.0, (datetime.now() - ts).total_seconds())
    except Exception:
        return False
    if age < float(min_wait_seconds):
        return False
    store.set_step_status(
        task_id,
        PDL_STEP_KEY,
        "skipped",
        message=f"全球人员信息检索超时（{int(age)}s），已跳过以免阻塞发文",
        payload={"source": "fail_forward", "hasHits": False, "staleSeconds": int(age)},
    )
    _after_pdl_done(store, task_id)
    return True


def apply_pdl_tool_result(
    store: Any,
    task_id: str,
    *,
    tool_output: str,
    success: bool,
) -> None:
    """Agent 补调 PDL 工具后的轻量收口（系统管线已终态则不改）。"""
    store.ensure_step_row(task_id, PDL_STEP_KEY)
    cur = get_step_status(task_id, PDL_STEP_KEY)
    if cur in {"completed", "skipped"}:
        return
    if not can_advance_to_pdl(task_id).get("ok"):
        return
    has_hit = False
    summary = None
    if success and tool_output:
        try:
            data = json.loads(tool_output)
        except Exception:
            data = None
        if isinstance(data, dict):
            persons = data.get("data")
            if isinstance(persons, list) and persons:
                has_hit = True
                p0 = persons[0] if isinstance(persons[0], dict) else {}
                summary = _summarize_person(p0)
            elif data.get("returned") and int(data.get("returned") or 0) > 0:
                has_hit = True
    if has_hit:
        hit_row = {"summary": summary} if summary else {}
        store.set_step_status(
            task_id,
            PDL_STEP_KEY,
            "completed",
            message="全球人员信息检索完成（Agent 补查有命中）",
            payload={
                "source": "agent_tool",
                "hasHits": True,
                "hits": [hit_row] if summary else [],
            },
        )
        if summary:
            _sync_pdl_hit_to_display(task_id, hit_row)
    else:
        store.set_step_status(
            task_id,
            PDL_STEP_KEY,
            "skipped",
            message="全球人员信息检索无命中（Agent 补查）",
            payload={"source": "agent_tool", "hasHits": False},
        )
    _after_pdl_done(store, task_id)
