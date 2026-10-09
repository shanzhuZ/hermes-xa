#!/usr/bin/env python3
"""把 mcp/mcp_servers.yaml 的 mcp_servers 段写进仓库根 config.yaml。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.yaml"
MCP_YAML = ROOT / "mcp" / "mcp_servers.yaml"


def extract_mcp_block(text: str) -> str:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == "mcp_servers:":
            start = i
            break
    if start is None:
        raise SystemExit("mcp_servers: not found in mcp/mcp_servers.yaml")
    return "\n".join(lines[start:]).rstrip() + "\n"


def replace_mcp_block(config_text: str, mcp_block: str) -> str:
    lines = config_text.splitlines()
    out = []
    skip = False
    inserted = False
    for line in lines:
        if line == "mcp_servers:":
            if not inserted:
                out.extend(mcp_block.rstrip("\n").splitlines())
                inserted = True
            skip = True
            continue
        if skip:
            if line and not line.startswith(" ") and line.endswith(":") and line != "mcp_servers:":
                skip = False
                out.append(line)
            continue
        out.append(line)
    if not inserted:
        final = []
        for line in out:
            if not inserted and line == "custom_providers:":
                final.extend(mcp_block.rstrip("\n").splitlines())
                inserted = True
            final.append(line)
        out = final
    if not inserted:
        out.append(mcp_block.rstrip("\n"))
    return "\n".join(out) + "\n"


def main() -> None:
    mcp_block = extract_mcp_block(MCP_YAML.read_text(encoding="utf-8"))
    new_text = replace_mcp_block(CONFIG.read_text(encoding="utf-8"), mcp_block)
    CONFIG.write_text(new_text, encoding="utf-8", newline="\n")
    print(f"merged into {CONFIG}")


if __name__ == "__main__":
    main()
