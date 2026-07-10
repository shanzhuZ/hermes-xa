"""写报步骤一种子解析（单种子，非核查多账号表格）。"""

from __future__ import annotations

import re
from typing import Any, Dict, List


def looks_like_seed_done(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 40:
        return False
    return bool(
        re.search(r"种子|profile|粉丝|简介|@([A-Za-z0-9_\.]+)", t, re.I)
    )


def parse_seed_from_assistant(*, user_message: str = "", assistant_text: str = "") -> List[Dict[str, Any]]:
    """兼容接口；写报单种子由 tool 落库驱动，此处仅返回空。"""
    return []
