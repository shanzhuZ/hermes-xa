#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""灌入首页统计索引 hermes_xa_homepage_stats。

用法：
  python scripts/homepage_stats/seed_es.py --recreate
"""

from __future__ import annotations

import argparse
import base64
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG: Dict[str, str] = {
    "host": "https://192.168.3.226:9201",
    "username": "elastic",
    "password": "i7Smzj2wVUndynjJXXv76A==",
}

INDEX_NAME = "hermes_xa_homepage_stats"
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SEED_PATH = HERE / "homepage_stats_seed.json"
MAPPING_PATH = (
    REPO_ROOT
    / "clients"
    / "hermes-xa"
    / "src"
    / "main"
    / "resources"
    / "es"
    / "homepage_stats_mapping.json"
)

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
    raw_body: Optional[bytes] = None,
) -> dict:
    host = (cfg.get("host") or "").rstrip("/")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--recreate", action="store_true")
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

    # 无 seed 则先生成
    if not SEED_PATH.is_file():
        from _build_seed import main as build_seed

        build_seed()

    if args.recreate and index_exists(cfg):
        print(f"删除索引 {INDEX_NAME} …")
        _es(cfg, "DELETE", INDEX_NAME)

    if not index_exists(cfg):
        mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        print(f"创建索引 {INDEX_NAME} …")
        _es(cfg, "PUT", INDEX_NAME, body=mapping)

    docs = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    lines = []
    for doc in docs:
        doc_id = str(doc.get("id") or "").strip()
        if not doc_id:
            raise ValueError(f"缺少 id: {doc}")
        lines.append(json.dumps({"index": {"_index": INDEX_NAME, "_id": doc_id}}, ensure_ascii=False))
        lines.append(json.dumps(doc, ensure_ascii=False))
    payload = ("\n".join(lines) + "\n").encode("utf-8")
    print(f"bulk 写入 {len(docs)} 条 …")
    result = _es(cfg, "POST", f"{INDEX_NAME}/_bulk", raw_body=payload)
    if result.get("errors"):
        print("bulk 有错误:", json.dumps(result, ensure_ascii=False)[:800], file=sys.stderr)
        return 1
    print("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
