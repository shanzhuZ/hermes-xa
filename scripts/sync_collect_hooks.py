#!/usr/bin/env python3
"""将 01 采集 db_sink Hook 同步到当前 Hermes 配置（默认 %LOCALAPPDATA%\\hermes）。"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_CMD = "D:/environment/python/python.exe D:/hermes-xa/scripts/db_sink.py"
EVENTS = ("pre_llm_call", "post_tool_call", "on_session_end")


def hermes_home() -> Path:
    val = os.environ.get("HERMES_HOME", "").strip()
    if val:
        return Path(val)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "hermes"
    return Path.home() / "AppData" / "Local" / "hermes"


def build_hooks_block() -> str:
    lines = ["hooks:"]
    for ev in EVENTS:
        lines.append(f"  {ev}:")
        lines.append(f"  - command: {HOOK_CMD}")
        lines.append("    timeout: 120")
    lines.append("hooks_auto_accept: true")
    return "\n".join(lines) + "\n"


def patch_config(config_path: Path) -> None:
    text = config_path.read_text(encoding="utf-8")
    block = build_hooks_block()
    patterns = [
        re.compile(r"(?ms)^hooks:\r?\n.*?^(?=hooks_auto_accept:|personalities:|security:|known_plugin)"),
        re.compile(r"(?ms)^hooks_auto_accept:.*?\r?\nhooks:\r?\n.*?^(?=known_plugin|personalities:|security:)"),
    ]
    for pattern in patterns:
        if pattern.search(text):
            text = pattern.sub(block, text, count=1)
            config_path.write_text(text, encoding="utf-8")
            print(f"已更新 hooks -> {HOOK_CMD}")
            print(f"配置文件: {config_path}")
            return
    print(f"未在 {config_path} 中找到 hooks 块，请手动对照 {REPO_ROOT / 'config.yaml'}", file=sys.stderr)
    sys.exit(1)


def patch_allowlist(allowlist_path: Path) -> None:
    data: dict = {"approvals": []}
    if allowlist_path.is_file():
        try:
            data = json.loads(allowlist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"approvals": []}
    approvals = [a for a in data.get("approvals", []) if a.get("command") != HOOK_CMD]
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for ev in EVENTS:
        approvals.append(
            {
                "event": ev,
                "command": HOOK_CMD,
                "approved_at": now,
                "script_mtime_at_approval": now,
            }
        )
    data["approvals"] = approvals
    allowlist_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"已更新 allowlist: {allowlist_path}")


def main() -> int:
    home = hermes_home()
    config_path = home / "config.yaml"
    if not config_path.is_file():
        print(f"未找到 Hermes 配置: {config_path}", file=sys.stderr)
        return 1
    patch_config(config_path)
    patch_allowlist(home / "shell-hooks-allowlist.json")
    print("\n请完全退出 Hermes 后重新启动，再发起采集任务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
