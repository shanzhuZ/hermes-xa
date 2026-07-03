#!/usr/bin/env python3
"""带代理补丁的 maigret CLI 启动器（供 MCP server 子进程调用）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保同目录 patch 可被 import
sys.path.insert(0, str(Path(__file__).resolve().parent))

from maigret_mcp_patch import apply_patches

apply_patches()

from maigret.maigret import main

if __name__ == "__main__":
    asyncio.run(main())
