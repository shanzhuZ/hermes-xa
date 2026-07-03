#!/usr/bin/env python3
"""实网测试 persona 返回体大小（需 TWITTER_PROXY + cookies）。"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import run_twitter_mcp as tw


async def main() -> None:
    tw._patch_get_client()
    import twitter_mcp.server as srv

    tw._register_persona_tools()
    screen = sys.argv[1] if len(sys.argv) > 1 else "whyyoutouzhele"
    raw = await tw._persona_impl(srv, screen, 90, 200, 0, 0, False)
    data = json.loads(raw)
    meta = data.get("_payload_meta") or {}
    pack = data.get("tweet_evidence_pack") or []
    keys = list(data.keys())[:5]
    print(f"screen_name={data.get('screen_name')}")
    print(f"payload_chars={meta.get('chars', len(raw)):,}")
    print(f"under_100k={meta.get('under_hermes_100k')}")
    print(f"evidence_count={len(pack)}")
    print(f"first_keys={keys}")
    if pack:
        print(f"first_evidence_date={pack[0].get('date')} likes={pack[0].get('likes')}")
        print(f"first_evidence_text={pack[0].get('text', '')[:120]}...")


if __name__ == "__main__":
    asyncio.run(main())
