"""通过 MCP stdio 协议调用 collect_accounts（最接近 Hermes 全链路）。"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HERMES_HOME = Path(os.environ.get("HERMES_HOME", r"C:/Users/zhr/AppData/Local/hermes"))

ENV = {
    "FASTMCP_SHOW_SERVER_BANNER": "false",
    "MAIGRET_REPORTS_DIR": str(HERMES_HOME / "maigret-reports"),
    "MAIGRET_PROXY": "http://127.0.0.1:7897",
    "MAIGRET_DEFAULT_TIMEOUT": "180",
    "MAIGRET_MAX_TIMEOUT": "180",
    "MAIGRET_GRACEFUL_STOP_SECONDS": "20",
    "MAIGRET_MAX_CONNECTIONS": "8",
    "MAIGRET_REQUEST_TIMEOUT": "12",
    "MAIGRET_RETRIES": "1",
    "PYTHONUNBUFFERED": "1",
}


async def main() -> None:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client
    from mcp.client.stdio import StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "server.py")],
        env={**os.environ, **ENV},
    )
    t0 = time.time()
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(
                "collect_accounts",
                {
                    "username": "whyyoutouzhele",
                    "top_sites": 8,
                    "timeout": 120,
                    "enable_recursion": False,
                },
            )
    elapsed = round(time.time() - t0, 1)
    text = ""
    for block in result.content or []:
        if hasattr(block, "text"):
            text += block.text
    try:
        payload = json.loads(text) if text.strip().startswith("{") else {"raw": text[:2000]}
    except json.JSONDecodeError:
        payload = {"raw": text[:2000]}
    print(
        json.dumps(
            {
                "layer": "mcp_stdio_protocol",
                "elapsed_s": elapsed,
                "timed_out": payload.get("timed_out"),
                "found": (payload.get("summary") or {}).get("found_count"),
                "report_json": payload.get("report_json"),
                "isError": result.isError,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
