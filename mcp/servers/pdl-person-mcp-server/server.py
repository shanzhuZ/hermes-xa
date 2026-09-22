#!/usr/bin/env python3
"""
全球人员信息检索-PDL MCP Server

上游：People Data Labs Person Search API
https://docs.peopledatalabs.com/docs/person-search-api
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Optional
from urllib.parse import quote

import requests
import urllib3
from mcp.server.fastmcp import FastMCP

if os.name == "nt":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for _stream in (sys.stdout, sys.stderr):
        _reconf = getattr(_stream, "reconfigure", None)
        if _reconf:
            try:
                _reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("pdl-person-mcp")

mcp = FastMCP("pdl_person")

PDL_API_KEY = (os.environ.get("PDL_API_KEY") or "").strip()
PDL_BASE_URL = (
    os.environ.get("PDL_BASE_URL") or "https://api.peopledatalabs.com/v5/person/search"
).strip()
DEFAULT_TIMEOUT = int(os.environ.get("PDL_TIMEOUT", "60"))
DEFAULT_PROXY = (
    os.environ.get("PDL_PROXY")
    or os.environ.get("HTTPS_PROXY")
    or os.environ.get("HTTP_PROXY")
    or ""
).strip()
MAX_SIZE = int(os.environ.get("PDL_MAX_SIZE", "25"))


def _normalize_social_link(social_link: str) -> str:
    """去掉协议与 www，便于 match_phrase profiles.url。"""
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


def _build_query(
    *,
    first_name: Optional[str] = None,
    middle_name: Optional[str] = None,
    last_name: Optional[str] = None,
    age: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    company_name: Optional[str] = None,
    company_title: Optional[str] = None,
    education_school: Optional[str] = None,
    education_degrees: Optional[str] = None,
    education_majors: Optional[str] = None,
    skill: Optional[str] = None,
    social_link: Optional[str] = None,
    input_phone: Optional[str] = None,
    input_email: Optional[str] = None,
) -> dict[str, Any]:
    """拼接 PDL Person Search 的 ES bool must 查询（保持原业务字段映射）。"""
    query: dict[str, Any] = {"query": {"bool": {"must": []}}}
    must = query["query"]["bool"]["must"]

    if first_name:
        must.append({"term": {"first_name": first_name}})
    if middle_name:
        must.append({"term": {"middle_name": middle_name}})
    if last_name:
        must.append({"term": {"last_name": last_name}})
    if age:
        must.append({"match": {"birth_year": age}})
    if city:
        must.append({"match": {"location_region": city}})
    if company_name:
        must.append({"match": {"experience.company.name": company_name}})
    if company_title:
        must.append({"match": {"experience.title.name": company_title}})
    if education_school:
        must.append({"match": {"education.school.name": education_school}})
    if education_degrees:
        must.append({"match": {"education.degrees": education_degrees}})
    if education_majors:
        must.append({"match": {"education.majors": education_majors}})
    if skill:
        must.append({"match": {"skills": skill}})

    normalized_link = ""
    if social_link:
        normalized_link = _normalize_social_link(social_link)
        if normalized_link:
            must.append({"match_phrase": {"profiles.url": normalized_link}})

    if input_phone:
        must.append({"match_phrase": {"phone_numbers": input_phone}})
    if input_email:
        must.append({"match_phrase": {"emails.address": input_email}})

    # 有社交链接时不加国家条件（与原脚本一致）
    if country and not normalized_link:
        must.append({"match": {"location_country": country}})

    return query


def _call_pdl(query: dict[str, Any], size: int) -> dict[str, Any]:
    if not PDL_API_KEY:
        return {
            "success": False,
            "error": "未配置 PDL_API_KEY，请在 .env 或 mcp_servers.env 中填写",
            "hint": "People Data Labs 需 API Key：https://www.peopledatalabs.com/",
        }
    if not query["query"]["bool"]["must"]:
        return {
            "success": False,
            "error": "至少提供一个检索条件（姓名、社交链接、邮箱、电话等）",
        }

    size = max(1, min(int(size or 1), MAX_SIZE))
    # 与原脚本一致：GET + query 放在 URL 参数里
    url = (
        f"{PDL_BASE_URL}"
        f"?query={quote(json.dumps(query, ensure_ascii=False), safe='')}"
        f"&size={size}&titlecase=false&pretty=false&dataset=all"
    )
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": PDL_API_KEY,
    }
    proxies = None
    if DEFAULT_PROXY:
        proxies = {"http": DEFAULT_PROXY, "https": DEFAULT_PROXY}

    logger.info("PDL 检索 size=%s must=%s", size, len(query["query"]["bool"]["must"]))
    try:
        resp = requests.get(
            url=url,
            headers=headers,
            proxies=proxies,
            timeout=DEFAULT_TIMEOUT,
            verify=False,
        )
    except requests.RequestException as exc:
        return {
            "success": False,
            "error": f"请求 PDL 失败：{exc}",
            "query": query,
        }

    try:
        body = resp.json()
    except Exception:
        body = {"raw": (resp.text or "")[:2000]}

    if resp.status_code != 200:
        return {
            "success": False,
            "status_code": resp.status_code,
            "error": body.get("error") if isinstance(body, dict) else body,
            "query": query,
            "hint": "检查 API Key、配额与检索条件",
        }

    data = body.get("data") if isinstance(body, dict) else None
    total = body.get("total") if isinstance(body, dict) else None
    return {
        "success": True,
        "status_code": resp.status_code,
        "total": total,
        "returned": len(data) if isinstance(data, list) else 0,
        "data": data if data is not None else body,
        "query": query,
        "hint": "MCP 仅返回结构化人员信息；画像/报告流程由 Skill 约束",
    }


@mcp.tool()
async def search_person(
    first_name: Optional[str] = None,
    middle_name: Optional[str] = None,
    last_name: Optional[str] = None,
    age: Optional[str] = None,
    city: Optional[str] = None,
    country: Optional[str] = None,
    company_name: Optional[str] = None,
    company_title: Optional[str] = None,
    education_school: Optional[str] = None,
    education_degrees: Optional[str] = None,
    education_majors: Optional[str] = None,
    skill: Optional[str] = None,
    social_link: Optional[str] = None,
    input_phone: Optional[str] = None,
    input_email: Optional[str] = None,
    max_num: int = 1,
) -> dict[str, Any]:
    """全球人员信息检索（People Data Labs）。

    按姓名、城市、公司、学校、社交主页、电话、邮箱等条件检索人员档案。
    至少传一个条件；social_link 示例：www.twitter.com/elonmusk。
    """
    query = _build_query(
        first_name=first_name,
        middle_name=middle_name,
        last_name=last_name,
        age=age,
        city=city,
        country=country,
        company_name=company_name,
        company_title=company_title,
        education_school=education_school,
        education_degrees=education_degrees,
        education_majors=education_majors,
        skill=skill,
        social_link=social_link,
        input_phone=input_phone,
        input_email=input_email,
    )
    return _call_pdl(query, max_num)


if __name__ == "__main__":
    mcp.run()
