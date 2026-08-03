#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首页统计定时增长（内部循环，每 2～3 分钟一次）。

用法：
  python scripts/homepage_stats/tick.py
  python scripts/homepage_stats/tick.py --once   # 只跑一轮便于调试
"""

from __future__ import annotations

import argparse
import base64
import json
import random
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CONFIG: Dict[str, str] = {
    "host": "https://192.168.3.226:9201",
    "username": "elastic",
    "password": "i7Smzj2wVUndynjJXXv76A==",
}

INDEX_NODE = "hermes_xa_agent_node"
INDEX_STATS = "hermes_xa_homepage_stats"
HERE = Path(__file__).resolve().parent
RULES_PATH = HERE / "biz_category_rules.json"

ssl_context = ssl._create_unverified_context()
_ES_OPENER = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context))


def _auth_header(username: str, password: str) -> Optional[str]:
    user = (username or "").strip()
    pwd = (password or "").strip()
    if not user or not pwd:
        return None
    encoded = base64.b64encode(f"{user}:{pwd}".encode()).decode()
    return f"Basic {encoded}"


def _es(
    cfg: Dict[str, str],
    method: str,
    path: str,
    body: Any = None,
    timeout: int = 60,
) -> dict:
    host = (cfg.get("host") or "").rstrip("/")
    url = f"{host}/{path.lstrip('/')}"
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    auth = _auth_header(cfg.get("username") or "", cfg.get("password") or "")
    if auth:
        headers["Authorization"] = auth
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with _ES_OPENER.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ES HTTP {exc.code} {method} {path}: {detail}") from exc


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def load_l2_rules() -> Dict[str, Optional[str]]:
    """返回 L2 id -> 写入字段名（None 表示只改 MCP 不进图表）"""
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    mapping: Dict[str, Optional[str]] = {}
    for rule in data.get("规则") or []:
        field = rule.get("写入字段")
        for l2 in rule.get("匹配L2") or []:
            mapping[str(l2)] = field
    return mapping


def fetch_all_nodes(cfg: Dict[str, str]) -> List[Dict[str, Any]]:
    body = {
        "size": 3000,
        "query": {"match_all": {}},
    }
    resp = _es(cfg, "POST", f"{INDEX_NODE}/_search", body=body)
    hits = (((resp.get("hits") or {}).get("hits")) or [])
    out = []
    for h in hits:
        src = h.get("_source") or {}
        src["_id"] = h.get("_id")
        out.append(src)
    return out


def resolve_l2(node: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> str:
    cur = node
    for _ in range(6):
        if not cur:
            return ""
        if int(cur.get("level") or 0) == 2:
            return str(cur.get("id") or "")
        pid = str(cur.get("parentId") or "")
        cur = by_id.get(pid)
    return ""


def bump_l4_account_counts(
    cfg: Dict[str, str],
    nodes: List[Dict[str, Any]],
    l2_field: Dict[str, Optional[str]],
) -> Tuple[int, int]:
    """随机挑若干 L4，各 +10~200。返回 (Δsocial, Δbusiness)。"""
    by_id = {str(n.get("id")): n for n in nodes if n.get("id")}
    l4 = [n for n in nodes if int(n.get("level") or 0) == 4]
    if not l4:
        print("警告：无 level=4 节点，跳过 MCP 增量")
        return 0, 0

    pick_n = random.randint(1, min(5, len(l4)))
    picked = random.sample(l4, pick_n)
    delta_social = 0
    delta_business = 0

    for n in picked:
        delta = random.randint(10, 200)
        nid = str(n.get("id"))
        # 更新 accountCount
        _es(
            cfg,
            "POST",
            f"{INDEX_NODE}/_update/{nid}",
            body={
                "script": {
                    "source": (
                        "if (ctx._source.stats == null) { ctx._source.stats = new HashMap(); } "
                        "if (ctx._source.stats.accountCount == null) { ctx._source.stats.accountCount = 0; } "
                        "ctx._source.stats.accountCount += params.delta;"
                    ),
                    "lang": "painless",
                    "params": {"delta": delta},
                }
            },
        )
        l2 = resolve_l2(n, by_id)
        field = l2_field.get(l2)
        if field == "socialCount":
            delta_social += delta
        elif field == "businessCount":
            delta_business += delta
        print(f"  L4 {nid} +{delta}  l2={l2}  chart={field or 'none'}")

    return delta_social, delta_business


def get_doc(cfg: Dict[str, str], index: str, doc_id: str) -> Optional[Dict[str, Any]]:
    try:
        resp = _es(cfg, "GET", f"{index}/_doc/{doc_id}")
    except RuntimeError as exc:
        if "404" in str(exc):
            return None
        raise
    if not resp.get("found"):
        return None
    return resp.get("_source") or {}


def put_doc(cfg: Dict[str, str], index: str, doc_id: str, doc: Dict[str, Any]) -> None:
    _es(cfg, "PUT", f"{index}/_doc/{doc_id}", body=doc)


def ensure_daily(cfg: Dict[str, str], date_str: str) -> Tuple[Dict[str, Any], bool]:
    """返回 (daily_doc, is_new)。"""
    doc_id = f"daily_{date_str}"
    existing = get_doc(cfg, INDEX_STATS, doc_id)
    if existing:
        return existing, False
    doc = {
        "id": doc_id,
        "type": "daily",
        "date": date_str,
        "verifyCount": 0,
        "reportCount": 0,
        "socialCount": 0,
        "businessCount": 0,
        "updatedAt": _now_str(),
        "remark": "tick",
    }
    return doc, True


def ensure_summary(cfg: Dict[str, str]) -> Dict[str, Any]:
    existing = get_doc(cfg, INDEX_STATS, "summary")
    if existing:
        return existing
    # 兜底写死值
    return {
        "id": "summary",
        "type": "summary",
        "todayUsage": 0,
        "historyTaskTotal": 0,
        "historyTaskCompletionRate": 98,
        "agentTotal": 1021,
        "agentOnline": 990,
        "agentOnlineRate": 97,
        "updatedAt": _now_str(),
        "remark": "tick",
    }


def tick_once(cfg: Dict[str, str], l2_field: Dict[str, Optional[str]]) -> None:
    print(f"\n===== tick {_now_str()} =====")
    nodes = fetch_all_nodes(cfg)
    delta_social, delta_business = bump_l4_account_counts(cfg, nodes, l2_field)

    date_str = _today()
    daily, is_new_day = ensure_daily(cfg, date_str)
    summary = ensure_summary(cfg)

    daily["socialCount"] = int(daily.get("socialCount") or 0) + delta_social
    daily["businessCount"] = int(daily.get("businessCount") or 0) + delta_business

    # 今日用量 +1~3，拆到写报/核查
    delta_today = random.randint(1, 3)
    delta_report = random.randint(0, delta_today)
    delta_verify = delta_today - delta_report

    daily["reportCount"] = int(daily.get("reportCount") or 0) + delta_report
    daily["verifyCount"] = int(daily.get("verifyCount") or 0) + delta_verify
    daily["updatedAt"] = _now_str()

    if is_new_day:
        summary["todayUsage"] = delta_today
    else:
        summary["todayUsage"] = int(summary.get("todayUsage") or 0) + delta_today
    summary["historyTaskTotal"] = int(summary.get("historyTaskTotal") or 0) + delta_today
    # 写死字段保持不变
    summary["historyTaskCompletionRate"] = 98
    summary["agentTotal"] = 1021
    summary["agentOnline"] = 990
    summary["agentOnlineRate"] = 97
    summary["updatedAt"] = _now_str()

    put_doc(cfg, INDEX_STATS, daily["id"], daily)
    put_doc(cfg, INDEX_STATS, "summary", summary)

    print(
        f"  charts +social={delta_social} +business={delta_business} "
        f"+report={delta_report} +verify={delta_verify}"
    )
    print(
        f"  summary todayUsage={summary['todayUsage']} "
        f"historyTaskTotal={summary['historyTaskTotal']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="首页统计内部循环增长")
    parser.add_argument("--host", default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--once", action="store_true", help="只执行一轮")
    parser.add_argument("--min-sleep", type=int, default=120, help="最短间隔秒，默认120")
    parser.add_argument("--max-sleep", type=int, default=180, help="最长间隔秒，默认180")
    args = parser.parse_args()

    cfg = {
        "host": (args.host or CONFIG.get("host") or "").strip(),
        "username": (args.user if args.user is not None else CONFIG.get("username") or "").strip(),
        "password": (
            args.password if args.password is not None else CONFIG.get("password") or ""
        ).strip(),
    }
    if not cfg["host"]:
        print("请配置 host", file=sys.stderr)
        return 1

    l2_field = load_l2_rules()
    print(f"ES={cfg['host']} 规则L2={list(l2_field.keys())}")

    if args.once:
        tick_once(cfg, l2_field)
        return 0

    print(f"内部循环启动：每 {args.min_sleep}~{args.max_sleep} 秒执行一次，Ctrl+C 退出")
    while True:
        try:
            tick_once(cfg, l2_field)
        except Exception as exc:
            print(f"tick 失败: {exc}", file=sys.stderr)
        sleep_sec = random.randint(args.min_sleep, args.max_sleep)
        print(f"下次执行约 {sleep_sec} 秒后…")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    sys.exit(main())
