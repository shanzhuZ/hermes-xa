#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从大屏索引 hermes_xa_agent_node 导出文档为 JSON。

与 seed_es.py 相同：连接信息用本文件 CONFIG 或命令行，不读仓库 .env。

用法：
  1) 改下方 CONFIG（或与 seed_es.py 保持一致）
  2) python scripts/agent_node/export_es.py
  3) python scripts/agent_node/export_es.py -o D:/tmp/agent_node.json
  4) python scripts/agent_node/export_es.py --query "{\"term\":{\"level\":1}}"
"""

from __future__ import annotations

import argparse
import base64
import json
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# ========== 大屏专用 ES（与 seed_es.py / tick.py 对齐）==========
CONFIG: Dict[str, str] = {
    "host": "http://loaclhost:9200",
    "username": "elastic",
    "password": "123456",
}
# ============================================================

INDEX_NAME = "hermes_xa_homepage_stats"
HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "hermes_xa_agent_node_export.json"

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
    timeout: int = 120,
) -> dict:
    host = (cfg.get("host") or "").rstrip("/")
    if not host:
        raise RuntimeError("未配置 ES host（改 CONFIG 或传 --host）")
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
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ES 连接失败: {exc.reason}") from exc


def resolve_config(args: argparse.Namespace) -> Dict[str, str]:
    return {
        "host": (args.host or CONFIG.get("host") or "").strip(),
        "username": (args.user if args.user is not None else CONFIG.get("username") or "").strip(),
        "password": (
            args.password if args.password is not None else CONFIG.get("password") or ""
        ).strip(),
    }


def parse_query(raw: Optional[str]) -> dict:
    """解析 --query JSON；空则 match_all。"""
    if not raw or not str(raw).strip():
        return {"match_all": {}}
    try:
        q = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"--query 不是合法 JSON: {exc}") from exc
    if not isinstance(q, dict):
        raise RuntimeError("--query 必须是 JSON 对象（ES query DSL）")
    # 允许用户直接传 {"match_all":{}} 或包一层 {"query":{...}}
    if "query" in q and len(q) == 1 and isinstance(q["query"], dict):
        return q["query"]
    return q


def scroll_export(
    cfg: Dict[str, str],
    query: dict,
    page_size: int,
    scroll: str,
    max_docs: int,
) -> List[Dict[str, Any]]:
    """scroll 拉全量（或到 max_docs）。"""
    docs: List[Dict[str, Any]] = []
    body = {
        "size": page_size,
        "query": query,
        "sort": ["_doc"],
    }
    first = _es(cfg, "POST", f"{INDEX_NAME}/_search?scroll={scroll}", body=body)
    scroll_id = first.get("_scroll_id")
    hits = (first.get("hits") or {}).get("hits") or []
    total = (first.get("hits") or {}).get("total")
    if isinstance(total, dict):
        total_n = total.get("value")
    else:
        total_n = total
    print(f"索引={INDEX_NAME} total≈{total_n} page_size={page_size}")

    try:
        while hits:
            for h in hits:
                docs.append(
                    {
                        "_id": h.get("_id"),
                        "_index": h.get("_index") or INDEX_NAME,
                        "_score": h.get("_score"),
                        "_source": h.get("_source") or {},
                    }
                )
                if max_docs > 0 and len(docs) >= max_docs:
                    print(f"已达 --max {max_docs}，停止拉取")
                    return docs
            if not scroll_id:
                break
            nxt = _es(
                cfg,
                "POST",
                "_search/scroll",
                body={"scroll": scroll, "scroll_id": scroll_id},
            )
            scroll_id = nxt.get("_scroll_id") or scroll_id
            hits = (nxt.get("hits") or {}).get("hits") or []
            print(f"  已拉取 {len(docs)} 条 …")
    finally:
        if scroll_id:
            try:
                _es(cfg, "DELETE", "_search/scroll", body={"scroll_id": [scroll_id]})
            except Exception:
                pass
    return docs


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 hermes_xa_agent_node 为 JSON")
    parser.add_argument("--host", default=None, help="ES 地址")
    parser.add_argument("--user", default=None, help="ES 用户名")
    parser.add_argument("--password", default=None, help="ES 密码")
    parser.add_argument(
        "-o",
        "--output",
        default=str(DEFAULT_OUT),
        help=f"输出 JSON 路径（默认 {DEFAULT_OUT}）",
    )
    parser.add_argument(
        "--query",
        default=None,
        help='ES query DSL JSON，如 {"term":{"level":1}}；默认 match_all',
    )
    parser.add_argument("--size", type=int, default=500, help="每批条数，默认 500")
    parser.add_argument("--scroll", default="2m", help="scroll 保活，默认 2m")
    parser.add_argument(
        "--max",
        type=int,
        default=0,
        help="最多导出条数，0 表示不限制",
    )
    parser.add_argument(
        "--source-only",
        action="store_true",
        help="输出仅 _source 数组（不含 _id）；默认带 _id/_source",
    )
    args = parser.parse_args()

    cfg = resolve_config(args)
    if not cfg["host"]:
        print("请在脚本 CONFIG 填写 host，或传 --host", file=sys.stderr)
        return 1

    try:
        query = parse_query(args.query)
        docs = scroll_export(
            cfg,
            query=query,
            page_size=max(1, args.size),
            scroll=args.scroll or "2m",
            max_docs=max(0, args.max),
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    out_path = Path(args.output).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.source_only:
        payload: Any = [d.get("_source") or {} for d in docs]
    else:
        payload = {
            "index": INDEX_NAME,
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "host": cfg["host"],
            "query": query,
            "count": len(docs),
            "docs": docs,
        }

    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已写入 {len(docs)} 条 -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
