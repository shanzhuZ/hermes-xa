#!/usr/bin/env python3
"""
Maigret 全链路诊断（③ Hermes MCP 客户端层）

与 dashboard 相同路径：Hermes config → mcp_tool.py → stdio → maigret-mcp-server → maigret CLI

用法（PowerShell）:

  $env:HERMES_HOME = "C:/Users/zhr/AppData/Local/hermes"
  python diag_hermes_maigret_chain.py whyyoutouzhele
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _setup_paths() -> Path:
    hermes_home = Path(
        os.environ.get("HERMES_HOME", "C:/Users/zhr/AppData/Local/hermes")
    ).resolve()
    agent_root = hermes_home / "hermes-agent"
    if not agent_root.is_dir():
        raise SystemExit(f"未找到 hermes-agent: {agent_root}")
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))
    os.environ.setdefault("HERMES_HOME", str(hermes_home))
    return hermes_home


def _load_maigret_timeout(hermes_home: Path) -> dict[str, Any]:
    from hermes_cli.config import load_config

    cfg = load_config()
    maigret = (cfg.get("mcp_servers") or {}).get("maigret") or {}
    return {
        "hermes_home": str(hermes_home),
        "config_file": str(hermes_home / "config.yaml"),
        "mcp_gateway_timeout_s": maigret.get("timeout", 120),
        "mcp_connect_timeout_s": maigret.get("connect_timeout", 60),
        "maigret_env": {
            k: v
            for k, v in (maigret.get("env") or {}).items()
            if k.startswith("MAIGRET_")
        },
    }


def _unwrap_mcp_payload(raw: Any) -> dict[str, Any]:
    """解析 Hermes registry.dispatch 返回（可能嵌套 result 字符串）。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return {"raw_preview": str(raw)[:2000]}

    text = raw.strip()
    if "---JSON_BELOW---" in text:
        text = text.split("---JSON_BELOW---", 1)[1].strip()

    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        return {"raw_preview": raw[:2000]}

    if isinstance(outer, dict) and "error" in outer:
        return outer

    inner = outer.get("result") if isinstance(outer, dict) else None
    if isinstance(inner, str):
        try:
            return json.loads(inner)
        except json.JSONDecodeError:
            return {"raw_preview": inner[:2000]}
    if isinstance(inner, dict):
        return inner
    return outer if isinstance(outer, dict) else {"raw_preview": raw[:2000]}


def _call_via_hermes(username: str, top_sites: int, scan_timeout: int) -> dict[str, Any]:
    from tools.mcp_tool import discover_mcp_tools, _load_mcp_config, _servers, _lock
    from tools.registry import registry

    tool_name = "mcp_maigret_collect_accounts"
    t_discover = time.time()
    registered = discover_mcp_tools()
    discover_s = round(time.time() - t_discover, 1)

    if tool_name not in registered:
        return {
            "ok": False,
            "error": f"未注册工具 {tool_name}",
            "registered_maigret_tools": [n for n in registered if "maigret" in n],
            "discover_s": discover_s,
        }

    with _lock:
        server = _servers.get("maigret")
    gateway_timeout = getattr(server, "tool_timeout", None) if server else None

    args = {
        "username": username,
        "top_sites": top_sites,
        "timeout": scan_timeout,
        "enable_recursion": False,
    }

    t0 = time.time()
    try:
        raw = registry.dispatch(tool_name, args)
    except Exception as exc:
        return {
            "ok": False,
            "layer": "hermes_mcp_gateway",
            "discover_s": discover_s,
            "gateway_tool_timeout_s": gateway_timeout,
            "elapsed_s": round(time.time() - t0, 1),
            "error": f"{type(exc).__name__}: {exc}",
        }
    elapsed = round(time.time() - t0, 1)

    payload = _unwrap_mcp_payload(raw)

    if isinstance(payload, dict) and payload.get("error"):
        return {
            "ok": False,
            "layer": "hermes_mcp_gateway",
            "discover_s": discover_s,
            "gateway_tool_timeout_s": gateway_timeout,
            "elapsed_s": elapsed,
            "error": payload.get("error"),
            "raw_preview": raw[:1500] if isinstance(raw, str) else None,
        }

    summary = payload.get("summary") or {}
    run = payload.get("run") or {}
    gateway_hit = (
        gateway_timeout is not None
        and elapsed >= float(gateway_timeout) - 2
        and not run.get("success")
        and not payload.get("timed_out")
    )
    scan_hit = bool(payload.get("timed_out")) and elapsed >= scan_timeout - 2

    if gateway_hit:
        diagnosis = "疑似 Hermes 网关 timeout 掐断（elapsed 接近 gateway_tool_timeout）"
    elif scan_hit:
        diagnosis = (
            "Maigret 内部 scan 超时（elapsed 接近 scan_timeout_requested）；"
            "report_json 为空则无任何结果，可试 --timeout 120"
        )
    elif payload.get("timed_out"):
        diagnosis = "Maigret 内部 scan 超时但可能有部分结果"
    elif run.get("success"):
        diagnosis = "全链路正常"
    else:
        diagnosis = "调用完成但未成功，见 stderr_tail / returncode"

    return {
        "ok": run.get("success") or bool(summary.get("found_count")),
        "layer": "hermes_mcp_gateway",
        "discover_s": discover_s,
        "gateway_tool_timeout_s": gateway_timeout,
        "scan_timeout_requested_s": scan_timeout,
        "elapsed_s": elapsed,
        "timed_out": payload.get("timed_out"),
        "scan_timeout_seconds": payload.get("scan_timeout_seconds"),
        "found_count": summary.get("found_count"),
        "partial_result": payload.get("partial_result"),
        "run_success": run.get("success"),
        "returncode": run.get("returncode"),
        "diagnosis": diagnosis,
        "stderr_tail": (run.get("stderr") or "")[-400:],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Maigret Hermes 全链路诊断")
    parser.add_argument("username", help="如 whyyoutouzhele")
    parser.add_argument("--top-sites", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=120, help="传给 collect_accounts 的 scan timeout")
    args = parser.parse_args()

    hermes_home = _setup_paths()
    meta = _load_maigret_timeout(hermes_home)

    print("=== Maigret 诊断 ③ Hermes MCP 全链路 ===")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    print()

    result = _call_via_hermes(args.username, args.top_sites, args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if not result.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
