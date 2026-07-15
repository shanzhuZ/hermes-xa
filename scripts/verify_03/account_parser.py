"""从用户消息或 Agent 步骤1 输出解析多平台种子账号。"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_PLATFORM_ALIASES = {
    "twitter": "twitter",
    "推特": "twitter",
    "x": "twitter",
    "facebook": "facebook",
    "fb": "facebook",
    "脸书": "facebook",
    "youtube": "youtube",
    "油管": "youtube",
    "instagram": "instagram",
    "ins": "instagram",
    "ig": "instagram",
    "telegram": "telegram",
    "tg": "telegram",
    "weibo": "weibo",
    "微博": "weibo",
    "bilibili": "bilibili",
    "b站": "bilibili",
    "哔哩哔哩": "bilibili",
    "tiktok": "tiktok",
    "抖音": "tiktok",
    "github": "github",
}

# 用户消息：推特账号：whyyoutouzhele
_MSG_PAIR = re.compile(
    r"(推特|twitter|x|facebook|fb|脸书|youtube|油管|instagram|ins|ig|telegram|tg|"
    r"微博|weibo|bilibili|b站|哔哩哔哩|tiktok|抖音|github)"
    r"\s*(?:账号|帐户|号)?\s*[:：]\s*([A-Za-z0-9_\.\u4e00-\u9fff]+)",
    re.I,
)

# Markdown 表格行：| 1 | 推特 (Twitter/X) | whyyoutouzhele |
_TABLE_ROW = re.compile(
    r"^\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|",
    re.M,
)


def _normalize_platform(token: str) -> Optional[str]:
    raw = (token or "").strip().lower()
    raw = raw.replace("（", "(").replace("）", ")")
    if "(" in raw:
        raw = raw.split("(")[0].strip()
    for key, plat in _PLATFORM_ALIASES.items():
        if key in raw:
            return plat
    return _PLATFORM_ALIASES.get(raw)


def _normalize_handle(handle: str) -> str:
    return (handle or "").strip().lstrip("@").strip()


def _dedupe(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out: List[Dict[str, Any]] = []
    for item in accounts:
        plat = str(item.get("platform") or "").strip()
        handle = _normalize_handle(str(item.get("account_handle") or ""))
        if not plat or not handle:
            continue
        key = (plat, handle.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "platform": plat,
                "account_handle": handle,
                "account_id": handle,
                "display_label": item.get("display_label"),
            }
        )
    return out


def parse_from_user_message(message: str) -> List[Dict[str, Any]]:
    accounts: List[Dict[str, Any]] = []
    for m in _MSG_PAIR.finditer(message or ""):
        plat = _normalize_platform(m.group(1))
        handle = _normalize_handle(m.group(2))
        if plat and handle:
            accounts.append({"platform": plat, "account_handle": handle, "account_id": handle})
    return _dedupe(accounts)


def parse_from_assistant_table(text: str) -> List[Dict[str, Any]]:
    accounts: List[Dict[str, Any]] = []
    for m in _TABLE_ROW.finditer(text or ""):
        plat_cell = m.group(1).strip()
        handle_cell = m.group(2).strip()
        if "平台" in plat_cell and "用户" in handle_cell:
            continue
        plat = _normalize_platform(plat_cell)
        handle = _normalize_handle(handle_cell)
        if plat and handle and handle.lower() not in {"用户名/handle", "用户名", "handle"}:
            accounts.append(
                {
                    "platform": plat,
                    "account_handle": handle,
                    "account_id": handle,
                    "display_label": plat_cell,
                }
            )
    return _dedupe(accounts)


def parse_input_accounts(*, user_message: str = "", assistant_text: str = "") -> List[Dict[str, Any]]:
    """优先 Agent 表格，其次用户原始消息。"""
    from_table = parse_from_assistant_table(assistant_text)
    if len(from_table) >= 1:
        return from_table
    from_msg = parse_from_user_message(user_message)
    if from_msg:
        return from_msg
    return []


def looks_like_step1_confirmation(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if "步骤1" in t or "步骤一" in t:
        if "种子" in t or "待核查" in t or "确认" in t:
            return True
    if "| 平台" in t and "|" in t:
        return True
    if _TABLE_ROW.search(t):
        return True
    return False
