#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generic MCP tool caller for Hermes account-intelligence tasks.
Usage: python mcp_call.py <server_name> <tool_name> '<json_args>'
"""
import json, os, sys, traceback
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

CONFIG_PATH = os.environ.get("HERMES_CONFIG", r"D:\hermes-xa\config.yaml")

def resolve_env(value, base_env):
    if not isinstance(value, str):
        return value
    if value.startswith("${") and value.endswith("}"):
        return base_env.get(value[2:-1], "")
    return value

def main():
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: mcp_call.py <server> <tool> [json_args]"}, ensure_ascii=False)); sys.exit(2)
    server_name, tool_name = sys.argv[1], sys.argv[2]
    tool_args = {}
    if len(sys.argv) >= 4 and sys.argv[3]:
        try:
            tool_args = json.loads(sys.argv[3])
        except Exception:
            print(json.dumps({"error": "invalid json args"}, ensure_ascii=False)); sys.exit(2)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    mcp_cfg = cfg.get("mcp_servers") or cfg.get("mcp") or {}
    servers = mcp_cfg.get("servers", mcp_cfg) if isinstance(mcp_cfg, dict) else {}
    if server_name not in servers:
        print(json.dumps({"error": f"server '{server_name}' not found; available: {list(servers.keys())}"}, ensure_ascii=False)); sys.exit(1)
    srv = servers[server_name]
    if not srv.get("enabled", True):
        print(json.dumps({"error": f"server '{server_name}' disabled"}, ensure_ascii=False)); sys.exit(1)
    base_env = dict(os.environ)
    merged = dict(base_env)
    for k, v in (srv.get("env") or {}).items():
        merged[k] = resolve_env(v, base_env)
    params = StdioServerParameters(command=srv["command"], args=list(srv.get("args") or []), env=merged, cwd=srv.get("cwd"))

    async def run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, tool_args)
                out = {"tool": tool_name, "args": tool_args}
                if result.isError:
                    out["isError"] = True
                texts = []
                for item in (result.content or []):
                    t = getattr(item, "type", "")
                    if t == "text":
                        texts.append(getattr(item, "text", ""))
                    elif t == "image":
                        texts.append(f"[image data:{getattr(item,'mimeType','')} len={len(getattr(item,'data','') or '')}]")
                out["content"] = texts
                if result.structuredContent is not None:
                    out["structuredContent"] = result.structuredContent
                print(json.dumps(out, ensure_ascii=False, default=str))

    try:
        import anyio
        anyio.run(run)
    except Exception:
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False)); sys.exit(1)

if __name__ == "__main__":
    main()
