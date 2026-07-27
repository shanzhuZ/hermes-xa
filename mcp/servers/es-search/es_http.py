"""ES 纯 HTTP 检索（无 mcp/pydantic 依赖）。

供 MCP server.py 与写报系统管线 report_04.osint_es 共用。
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, unquote

# ──────────────────────────────────────────────
# 0. 配置加载
# ──────────────────────────────────────────────

_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _v = _line.split("=", 1)
            _k, _v = _k.strip(), _v.strip().strip("\"'")
            if not os.environ.get(_k):
                os.environ[_k] = _v

# 凭证仅从环境变量 / config.yaml env 注入，禁止把密码写进仓库
ES_HOST = os.environ.get("ES_HOST", "http://127.0.0.1:9200")
ES_USERNAME = os.environ.get("ES_USERNAME", "")
ES_PASSWORD = os.environ.get("ES_PASSWORD", "")

# ──────────────────────────────────────────────
# 1. 索引定义（一个索引起一个 tool）
# ──────────────────────────────────────────────

INDICES = [
    {
        "name": "country_wise",
        "index_name": "dw_identity_info_lk_country_wise_new_all",
        "display": "全球身份信息（按国家）",
        "description": "检索全球身份信息库，支持姓名和社交账号搜索",
        "search_fields": ["full_name", "linkedin_url", "twitter_url", "facebook_url"],
        "page_size": 20,
    },
    {
        "name": "facebook",
        "index_name": "dw_identity_info_facebook",
        "display": "Facebook 身份信息",
        "description": "检索 Facebook 用户身份数据",
        "search_fields": ["name"],
        "page_size": 20,
    },
    {
        "name": "worldpeople",
        "index_name": "dw_worldpeople_250807711_data",
        "display": "全球人口数据",
        "description": "检索全球人口信息，支持姓和名搜索",
        "search_fields": ["First_Name_01", "Last_Name_01"],
        "page_size": 20,
    },
]

# ──────────────────────────────────────────────
# 2. ES HTTP 客户端（纯 stdlib）
# ──────────────────────────────────────────────


def _auth_header() -> str | None:
    if ES_USERNAME and ES_PASSWORD:
        encoded = base64.b64encode(f"{ES_USERNAME}:{ES_PASSWORD}".encode()).decode()
        return f"Basic {encoded}"
    return None


AUTH = _auth_header()


# 内网 ES 禁止走 HTTP(S)_PROXY，否则易 502/挂起
_ES_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _es(method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    """发送 ES HTTP 请求，返回 JSON 结果。"""
    url = urljoin(ES_HOST.rstrip("/") + "/", path.lstrip("/"))
    data = json.dumps(body).encode("utf-8") if body else None
    headers = {"Content-Type": "application/json"}
    if AUTH:
        headers["Authorization"] = AUTH
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _ES_OPENER.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ES HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"ES 连接失败: {e.reason}")
    except OSError as e:
        raise RuntimeError(f"ES 网络错误: {e}")


# ──────────────────────────────────────────────
# 3. 业务搜索逻辑
# ──────────────────────────────────────────────

_URL_FIELD_BY_HOST = (
    (("twitter.com", "x.com", "mobile.twitter.com"), "twitter_url"),
    (("facebook.com", "fb.com", "www.facebook.com", "m.facebook.com"), "facebook_url"),
    (("linkedin.com", "www.linkedin.com"), "linkedin_url"),
)


def _strip_url_noise(path: str) -> str:
    path = (path or "").strip("/")
    if not path:
        return ""
    # 去掉 query/fragment 残留与常见尾段
    path = path.split("?")[0].split("#")[0]
    parts = [p for p in path.split("/") if p and p.lower() not in {"status", "posts", "photos"}]
    if not parts:
        return ""
    # linkedin: in/xxx
    if parts[0].lower() == "in" and len(parts) >= 2:
        return f"in/{parts[1]}"
    # facebook profile.php?id= 已在上层处理；普通 path 取首段
    return parts[0]


def _normalize_social_query(query_text: str) -> Dict[str, Any]:
    """把完整 URL / @handle 规范成库内常见形态：twitter.com/xxx。"""
    raw = (query_text or "").strip()
    out: Dict[str, Any] = {
        "raw": raw,
        "is_url": False,
        "is_handle": False,
        "is_name": False,
        "field_hint": None,
        "normalized": None,  # twitter.com/handle
        "handle": None,
        "variants": [],  # term/wildcard 候选
    }
    if not raw:
        return out

    text = raw
    # 纯 @handle
    if re.fullmatch(r"@?[A-Za-z0-9_]{1,30}", text) and "://" not in text and "/" not in text:
        handle = text.lstrip("@")
        out["is_handle"] = True
        out["handle"] = handle
        out["field_hint"] = "twitter_url"
        out["normalized"] = f"twitter.com/{handle}"
        out["variants"] = [
            f"twitter.com/{handle}",
            f"https://twitter.com/{handle}",
            f"https://x.com/{handle}",
            handle,
        ]
        return out

    # URL
    if "://" not in text and ("." in text or text.startswith("www.")):
        # twitter.com/xxx 无 scheme
        if re.match(r"^(twitter\.com|x\.com|facebook\.com|fb\.com|linkedin\.com)/", text, re.I):
            text = "https://" + text
    if "://" in text or text.lower().startswith("www."):
        if text.lower().startswith("www."):
            text = "https://" + text
        try:
            parsed = urlparse(text)
        except Exception:
            parsed = None
        if parsed and parsed.netloc:
            host = (parsed.netloc or "").lower()
            if host.startswith("www."):
                host = host[4:]
            field_hint = None
            for hosts, field in _URL_FIELD_BY_HOST:
                if host in hosts or any(host.endswith("." + h) for h in hosts):
                    field_hint = field
                    break
            path = unquote(parsed.path or "")
            # facebook.com/profile.php?id=
            qs = parsed.query or ""
            if field_hint == "facebook_url" and "profile.php" in path and "id=" in qs:
                m = re.search(r"(?:^|&)id=(\d+)", qs)
                handle = m.group(1) if m else ""
                normalized = f"facebook.com/profile.php?id={handle}" if handle else None
            else:
                handle = _strip_url_noise(path)
                if field_hint == "twitter_url":
                    normalized = f"twitter.com/{handle}" if handle else None
                elif field_hint == "facebook_url":
                    normalized = f"facebook.com/{handle}" if handle else None
                elif field_hint == "linkedin_url":
                    normalized = f"linkedin.com/{handle}" if handle else None
                else:
                    normalized = f"{host}/{handle}" if handle else host

            out["is_url"] = True
            out["field_hint"] = field_hint
            out["handle"] = handle or None
            out["normalized"] = normalized
            variants = []
            if normalized:
                variants.extend(
                    [
                        normalized,
                        f"https://{normalized}",
                        f"http://{normalized}",
                        f"www.{normalized}",
                    ]
                )
            if handle and field_hint == "twitter_url":
                variants.extend(
                    [
                        f"x.com/{handle}",
                        f"https://x.com/{handle}",
                        f"https://twitter.com/{handle}",
                        handle,
                    ]
                )
            # 去重保序
            seen = set()
            uniq = []
            for v in variants:
                if v and v not in seen:
                    seen.add(v)
                    uniq.append(v)
            out["variants"] = uniq
            return out

    # 其余当人名
    out["is_name"] = True
    out["field_hint"] = "full_name"
    out["normalized"] = raw
    return out


def _term_url_clauses(field: str, variants: List[str]) -> List[dict]:
    """仅 term 精确（快）。含大小写变体。"""
    clauses: List[dict] = []
    seen = set()
    expanded: List[str] = []
    for v in variants:
        if not v:
            continue
        for cand in (v, v.lower()):
            if cand not in seen:
                seen.add(cand)
                expanded.append(cand)
        if len(expanded) >= 6:
            break
    for v in expanded:
        clauses.append({"term": {f"{field}.keyword": v}})
        clauses.append({"term": {field: v}})
    return clauses


def _name_clauses(person_name: str) -> List[dict]:
    """人名：只用 match_phrase，避免中文被拆成「老师」等假阳性。"""
    name = (person_name or "").strip()
    if not name:
        return []
    return [{"match_phrase": {"full_name": {"query": name, "boost": 5}}}]


def _build_country_wise_es_query(
    query_text: str,
    field_filter: Optional[str] = None,
    person_name: Optional[str] = None,
    *,
    url_only: bool = False,
    name_only: bool = False,
) -> Tuple[dict, Dict[str, Any]]:
    """构造 country_wise 查询：URL term 精确 + 人名 phrase。"""
    norm = _normalize_social_query(query_text)
    should: List[dict] = []
    modes: List[str] = []

    url_field = field_filter if field_filter in {"twitter_url", "facebook_url", "linkedin_url"} else None
    if not url_field and norm.get("field_hint") in {"twitter_url", "facebook_url", "linkedin_url"}:
        url_field = norm["field_hint"]

    name = (person_name or "").strip()
    if not name and (norm.get("is_name") or field_filter == "full_name"):
        name = query_text.strip()

    do_url = not name_only and url_field and (
        norm.get("is_url") or norm.get("is_handle") or field_filter in {
            "twitter_url", "facebook_url", "linkedin_url"
        }
    )
    do_name = not url_only and bool(name) and (
        field_filter in {None, "full_name"} or bool(person_name) or norm.get("is_name")
    )

    if do_url:
        modes.append(f"url_term:{url_field}")
        variants = list(norm.get("variants") or [])
        if not variants and query_text.strip():
            variants = [query_text.strip()]
        # 保证库内形态在最前
        if norm.get("normalized"):
            variants = [norm["normalized"]] + [v for v in variants if v != norm["normalized"]]
        should.extend(_term_url_clauses(url_field, variants))

    if do_name:
        modes.append("name_phrase:full_name")
        should.extend(_name_clauses(name))

    if not should:
        modes.append("fallback:multi_match")
        fields = [field_filter] if field_filter else [
            "full_name", "linkedin_url", "twitter_url", "facebook_url"
        ]
        should.append(
            {
                "multi_match": {
                    "query": query_text,
                    "fields": fields,
                    "type": "phrase",
                    "operator": "and",
                }
            }
        )

    es_query = {"bool": {"should": should, "minimum_should_match": 1}}
    meta = {
        "modes": modes,
        "normalized": norm.get("normalized"),
        "handle": norm.get("handle"),
        "field_hint": norm.get("field_hint"),
        "person_name": name or None,
        "variants": (norm.get("variants") or [])[:8],
    }
    return es_query, meta


def _search_index(
    index_name: str,
    query_text: str,
    fields: list,
    size: int = 20,
    field_filter: Optional[str] = None,
    *,
    person_name: Optional[str] = None,
    smart: bool = False,
) -> Tuple[dict, Dict[str, Any]]:
    """搜索指定索引。country_wise：先 URL term，0 命中再补人名 phrase。"""
    meta: Dict[str, Any] = {"modes": ["multi_match"]}
    size = min(size, 100)
    if not smart:
        match_fields = [field_filter] if field_filter else fields
        es_query = {
            "query": {
                "multi_match": {
                    "query": query_text,
                    "fields": match_fields,
                    "type": "best_fields",
                    "operator": "or",
                }
            },
            "size": size,
        }
        return _es("POST", f"/{index_name}/_search", es_query), meta

    # 1) URL term 精确（若输入是 URL/handle）
    q_url, meta_url = _build_country_wise_es_query(
        query_text, field_filter=field_filter, person_name=person_name, url_only=True
    )
    took = 0
    has_url_phase = any(str(m).startswith("url_") for m in (meta_url.get("modes") or []))
    empty = {"hits": {"total": 0, "hits": []}, "took": 0}
    resp = empty
    if has_url_phase:
        resp = _es("POST", f"/{index_name}/_search", {"query": q_url, "size": size})
        took += int(resp.get("took") or 0)
        if _total_hits(resp) > 0:
            meta_url["modes"] = list(meta_url.get("modes") or []) + ["phase:url_hit"]
            meta_url["took_ms_phases"] = [took]
            resp["took"] = took
            return resp, meta_url

    # 2) 人名 phrase（显式 person_name，或 query 本身是人名）
    q_name, meta_name = _build_country_wise_es_query(
        query_text, field_filter=field_filter, person_name=person_name, name_only=True
    )
    has_name_phase = any(str(m).startswith("name_") for m in (meta_name.get("modes") or []))
    if has_name_phase:
        resp2 = _es("POST", f"/{index_name}/_search", {"query": q_name, "size": size})
        took2 = int(resp2.get("took") or 0)
        took += took2
        modes = []
        if has_url_phase:
            modes.append("phase:url_miss")
            modes.extend(meta_url.get("modes") or [])
        modes.extend(meta_name.get("modes") or [])
        modes.append("phase:name")
        meta_name["modes"] = modes
        meta_name["normalized"] = meta_url.get("normalized") or meta_name.get("normalized")
        meta_name["took_ms_phases"] = [took - took2, took2]
        resp2["took"] = took
        return resp2, meta_name

    # 3) 仅有 URL 且无命中、也无人名 → 返回空（避免再 multi_match 噪声）
    if has_url_phase:
        meta_url["modes"] = list(meta_url.get("modes") or []) + ["phase:url_miss_empty"]
        meta_url["took_ms_phases"] = [took]
        resp["took"] = took
        return resp, meta_url

    # 4) fallback
    q_fb, meta_fb = _build_country_wise_es_query(
        query_text, field_filter=field_filter, person_name=person_name
    )
    resp3 = _es("POST", f"/{index_name}/_search", {"query": q_fb, "size": size})
    took += int(resp3.get("took") or 0)
    meta_fb["modes"] = list(meta_fb.get("modes") or []) + ["phase:fallback"]
    meta_fb["took_ms_phases"] = [took]
    resp3["took"] = took
    return resp3, meta_fb


def _format_hits(resp: dict) -> list:
    """从 ES 响应中提取 _source + _id + _score。"""
    hits = resp.get("hits", {}).get("hits", [])
    return [
        {"_id": h["_id"], "_score": round(h.get("_score", 0), 4),
         **h.get("_source", {})}
        for h in hits
    ]


def _total_hits(resp: dict) -> int:
    """兼容 ES 7.x 和 8.x 的 total hits 取值。"""
    total = resp.get("hits", {}).get("total", 0)
    if isinstance(total, dict):
        return total.get("value", 0)
    return total



def search_country_wise(
    query_text: str,
    *,
    field: Optional[str] = None,
    person_name: Optional[str] = None,
    size: int = 5,
) -> Dict[str, Any]:
    """系统/测试入口：country_wise 精确检索，返回与 MCP 工具同形结果。"""
    idx = next(i for i in INDICES if i["name"] == "country_wise")
    resp, meta = _search_index(
        idx["index_name"],
        query_text,
        idx["search_fields"],
        size,
        field,
        person_name=person_name,
        smart=True,
    )
    hits = _format_hits(resp)
    return {
        "tool": "search_country_wise",
        "index": idx["index_name"],
        "display": idx["display"],
        "query": query_text,
        "filtered_field": field,
        "person_name": person_name,
        "normalized": meta.get("normalized"),
        "query_modes": meta.get("modes"),
        "took_ms": resp.get("took"),
        "total_hits": _total_hits(resp),
        "returned": len(hits),
        "results": hits,
        "source": "http_pipeline",
    }
