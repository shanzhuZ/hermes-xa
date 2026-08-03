#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""灌入大屏假数据索引 agent_node。

大屏 ES 与 MCP 社工库 ES 分离：连接信息只在本文件 CONFIG 或命令行配置，
不读取仓库 .env / 环境变量。

用法：
  1) 先改下方 CONFIG
  2) python scripts/agent_node/seed_es.py --recreate
  或：python scripts/agent_node/seed_es.py --host http://x:9200 --user u --password p --recreate
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

# ========== 大屏专用 ES（自行填写，勿与 MCP ES 混用）==========
CONFIG: Dict[str, str] = {
    "host": "https://192.168.3.226:9201",
    "username": "elastic",
    "password": "i7Smzj2wVUndynjJXXv76A==",
}
# ============================================================

INDEX_NAME = "hermes_xa_agent_node"
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SEED_PATH = HERE / "agent_node_seed.json"
MAPPING_PATH = (
    REPO_ROOT
    / "clients"
    / "hermes-xa"
    / "src"
    / "main"
    / "resources"
    / "es"
    / "agent_node_mapping.json"
)
import ssl
# # 内网 ES 禁止走 HTTP(S)_PROXY
# _ES_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
ssl_context = ssl._create_unverified_context()

_ES_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=ssl_context)
)


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
    raw_body: Optional[bytes] = None,
) -> dict:
    host = (cfg.get("host") or "").rstrip("/")
    if not host:
        raise RuntimeError("未配置 ES host（改 CONFIG 或传 --host）")
    url = f"{host}/{path.lstrip('/')}"
    if raw_body is not None:
        data = raw_body
        content_type = "application/x-ndjson"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        content_type = "application/json"
    else:
        data = None
        content_type = "application/json"
    headers = {"Content-Type": content_type}
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


def index_exists(cfg: Dict[str, str]) -> bool:
    host = (cfg.get("host") or "").rstrip("/")
    url = f"{host}/{INDEX_NAME}"
    headers: Dict[str, str] = {}
    auth = _auth_header(cfg.get("username") or "", cfg.get("password") or "")
    if auth:
        headers["Authorization"] = auth
    req = urllib.request.Request(url, method="HEAD", headers=headers)
    try:
        with _ES_OPENER.open(req, timeout=30) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ES HEAD {INDEX_NAME} HTTP {exc.code}: {detail}") from exc


def delete_index(cfg: Dict[str, str]) -> None:
    print(f"删除索引 {INDEX_NAME} …")
    _es(cfg, "DELETE", INDEX_NAME)


def create_index(cfg: Dict[str, str]) -> None:
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    print(f"创建索引 {INDEX_NAME} …")
    _es(cfg, "PUT", INDEX_NAME, body=mapping)


def bulk_seed(cfg: Dict[str, str], docs: list) -> dict:
    lines = []
    for doc in docs:
        doc_id = str(doc.get("id") or "").strip()
        if not doc_id:
            raise ValueError(f"种子文档缺少 id: {doc}")
        lines.append(
            json.dumps({"index": {"_index": INDEX_NAME, "_id": doc_id}}, ensure_ascii=False)
        )
        lines.append(json.dumps(doc, ensure_ascii=False))
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    print(f"bulk 写入 {len(docs)} 条 …")
    return _es(cfg, "POST", f"{INDEX_NAME}/_bulk", raw_body=payload)


def resolve_config(args: argparse.Namespace) -> Dict[str, str]:
    """命令行优先，否则用文件顶部 CONFIG。"""
    return {
        "host": (args.host or CONFIG.get("host") or "").strip(),
        "username": (args.user if args.user is not None else CONFIG.get("username") or "").strip(),
        "password": (
            args.password if args.password is not None else CONFIG.get("password") or ""
        ).strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="灌入 agent_node 假数据（大屏专用 ES，不读 .env）"
    )
    parser.add_argument("--host", default=None, help="ES 地址，如 http://192.168.x.x:9200")
    parser.add_argument("--user", default=None, help="ES 用户名（无鉴权可省略）")
    parser.add_argument("--password", default=None, help="ES 密码（无鉴权可省略）")
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="若索引已存在则先删除再重建",
    )
    args = parser.parse_args()

    cfg = resolve_config(args)
    if not cfg["host"]:
        print("请在脚本 CONFIG 填写 host，或传 --host", file=sys.stderr)
        return 1
    print(f"ES_HOST={cfg['host']} user={cfg['username'] or '(none)'}")

    if not MAPPING_PATH.is_file():
        print(f"缺少 mapping: {MAPPING_PATH}", file=sys.stderr)
        return 1
    if not SEED_PATH.is_file():
        print(f"缺少 seed: {SEED_PATH}", file=sys.stderr)
        return 1

    docs = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    if not isinstance(docs, list) or not docs:
        print("seed 必须是非空 JSON 数组", file=sys.stderr)
        return 1

    exists = index_exists(cfg)
    if exists and args.recreate:
        delete_index(cfg)
        exists = False
    elif exists and not args.recreate:
        print(f"索引 {INDEX_NAME} 已存在；加 --recreate 可重建，或直接 bulk 覆盖同 id 文档")

    if not exists:
        create_index(cfg)

    result = bulk_seed(cfg, docs)
    errors = result.get("errors")
    items = result.get("items") or []
    failed = []
    if errors:
        for item in items:
            idx = (item or {}).get("index") or {}
            if idx.get("error"):
                failed.append({"id": idx.get("_id"), "error": idx.get("error")})
    if failed:
        print(f"bulk 部分失败 {len(failed)} 条:", file=sys.stderr)
        for row in failed[:10]:
            print(row, file=sys.stderr)
        return 1

    _es(cfg, "POST", f"{INDEX_NAME}/_refresh")
    count = _es(cfg, "GET", f"{INDEX_NAME}/_count")
    print(f"完成：索引={INDEX_NAME} count={count.get('count')} seed={len(docs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
