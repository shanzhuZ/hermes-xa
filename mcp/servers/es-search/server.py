#!/usr/bin/env python3
"""
Elasticsearch MCP Server — 业务数据检索

三个内置索引，每个索引一个独立搜索工具：
  1. search_country_wise   → dw_identity_info_lk_country_wise_new_all
  2. search_facebook       → dw_identity_info_facebook
  3. search_worldpeople    → dw_worldpeople_250807711_data

查询逻辑在 es_http.py（纯 stdlib），本文件只负责 MCP 协议封装。
"""

import asyncio
import json

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.types import Tool, TextContent

from es_http import (
    ES_HOST,
    INDICES,
    _es,
    _format_hits,
    _search_index,
    _total_hits,
)

# ──────────────────────────────────────────────
# MCP Server
# ──────────────────────────────────────────────

server = Server("es-search")


@server.list_tools()
async def list_tools() -> list[Tool]:
    tools: list[Tool] = []

    for idx in INDICES:
        is_country = idx["name"] == "country_wise"
        props = {
            "query_text": {
                "type": "string",
                "description": (
                    "搜索关键词。写报场景优先传 profile_url；"
                    "会自动规范为 twitter.com/handle 等库内形态做精确匹配。"
                    if is_country
                    else "搜索关键词（如姓名、URL、账号）"
                ),
            },
            "field": {
                "type": "string",
                "enum": idx["search_fields"],
                "description": "限定搜索字段（不选则按 URL/人名自动推断）",
            },
            "size": {
                "type": "integer",
                "description": "返回条数",
                "default": idx.get("page_size", 20),
                "minimum": 1,
                "maximum": 100,
            },
        }
        if is_country:
            props["person_name"] = {
                "type": "string",
                "description": (
                    "可选人名（如「李老师不是你老师」/ Elon Musk）。"
                    "与 URL 查询一起做 should 匹配；也可单独用人名检索。"
                ),
            }
        desc = (
            f"搜索 {idx['display']} — {idx['description']}"
            f"\n索引: {idx['index_name']}"
            f"\n可检索字段: {', '.join(idx['search_fields'])}"
        )
        if is_country:
            desc += (
                "\nURL 会规范为 twitter.com/xxx、facebook.com/xxx、linkedin.com/in/xxx 后精确匹配；"
                "可同时传 person_name 做人名匹配。"
            )
        tools.append(
            Tool(
                name=f"search_{idx['name']}",
                description=desc,
                inputSchema={
                    "type": "object",
                    "properties": props,
                    "required": ["query_text"],
                },
            )
        )

    tools.append(
        Tool(
            name="es_raw_query",
            description="用任意 ES Query DSL 搜索索引（高级用法）",
            inputSchema={
                "type": "object",
                "properties": {
                    "index": {"type": "string", "description": "目标索引名"},
                    "query_body": {
                        "type": "object",
                        "description": "ES Query DSL body（不需要包含 size）",
                    },
                    "size": {
                        "type": "integer",
                        "description": "返回条数",
                        "default": 20,
                        "maximum": 100,
                    },
                },
                "required": ["index", "query_body"],
            },
        )
    )
    tools.append(
        Tool(
            name="list_es_indices",
            description="列出 ES 集群中所有非系统索引及其文档数",
            inputSchema={"type": "object", "properties": {}},
        )
    )
    tools.append(
        Tool(
            name="es_cluster_health",
            description="检查 ES 集群健康状态",
            inputSchema={"type": "object", "properties": {}},
        )
    )
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        result = await _dispatch(name, arguments)
        return [
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, indent=2),
            )
        ]
    except Exception as e:
        return [
            TextContent(
                type="text",
                text=json.dumps({"error": str(e)}, ensure_ascii=False, indent=2),
            )
        ]


async def _dispatch(name: str, args: dict) -> dict:
    """路由到对应处理函数。"""

    if name.startswith("search_"):
        tool_key = name[len("search_") :]
        idx_cfg = next((i for i in INDICES if i["name"] == tool_key), None)
        if not idx_cfg:
            raise ValueError(f"未知工具: {name}")

        actual_index = idx_cfg["index_name"]
        query = args["query_text"]
        field_filter = args.get("field", None)
        size = args.get("size", idx_cfg.get("page_size", 20))
        person_name = args.get("person_name")
        smart = tool_key == "country_wise"

        resp, meta = _search_index(
            actual_index,
            query,
            idx_cfg["search_fields"],
            size,
            field_filter,
            person_name=person_name,
            smart=smart,
        )
        hits = _format_hits(resp)
        total = _total_hits(resp)

        return {
            "tool": name,
            "index": idx_cfg["index_name"],
            "display": idx_cfg["display"],
            "query": query,
            "filtered_field": field_filter,
            "person_name": person_name,
            "normalized": meta.get("normalized"),
            "query_modes": meta.get("modes"),
            "took_ms": resp.get("took"),
            "total_hits": total,
            "returned": len(hits),
            "results": hits,
        }

    if name == "es_raw_query":
        resp = _es(
            "POST",
            f"/{args['index']}/_search",
            {**args.get("query_body", {}), "size": min(args.get("size", 20), 100)},
        )
        return {
            "index": args["index"],
            "total_hits": _total_hits(resp),
            "returned": len(resp.get("hits", {}).get("hits", [])),
            "results": _format_hits(resp),
        }

    if name == "list_es_indices":
        raw = _es("GET", "/_cat/indices?format=json&h=index,docs.count,store.size")
        indices = [r for r in raw if not r.get("index", "").startswith(".")]
        return {"cluster": ES_HOST, "indices": indices}

    if name == "es_cluster_health":
        h = _es("GET", "/_cluster/health")
        return {
            "cluster_name": h.get("cluster_name"),
            "status": h.get("status"),
            "nodes": h.get("number_of_nodes"),
            "data_nodes": h.get("number_of_data_nodes"),
            "active_shards": h.get("active_shards"),
            "unassigned_shards": h.get("unassigned_shards"),
        }

    raise ValueError(f"未知工具: {name}")


async def main():
    from mcp.server.stdio import stdio_server

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="es-search",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
