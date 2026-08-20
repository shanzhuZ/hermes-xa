"""MCP stdio probe: initialize -> tools/list -> tools/call get_user_info.

用法: python probe_mcp.py <server_script> <tool_name> <json_args>
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")


async def main() -> int:
    script = sys.argv[1]
    tool = sys.argv[2] if len(sys.argv) > 2 else "get_user_info"
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        script,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def send(obj: dict) -> None:
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    async def recv() -> dict:
        line = await proc.stdout.readline()
        if not line:
            return {}
        return json.loads(line.decode("utf-8", errors="replace"))

    await send(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1.0"},
            },
        }
    )
    init = await recv()
    print("INIT:", json.dumps(init, ensure_ascii=False)[:300])
    await send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    await send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tl = await recv()
    tools = [t["name"] for t in (tl.get("result", {}).get("tools", []) or [])]
    print("TOOLS:", tools)
    if tool not in tools:
        print("TOOL MISSING:", tool)
        proc.kill()
        return 1
    await send(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        }
    )
    result = await recv()
    print("CALL RESULT:", json.dumps(result, ensure_ascii=False)[:2500])
    proc.kill()
    err = await proc.stderr.read()
    if err:
        print("STDERR:", err.decode("utf-8", errors="replace")[-1500:])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
