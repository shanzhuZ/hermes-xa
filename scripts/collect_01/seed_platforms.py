"""种子账号主页工具 — MCP + Apify（01/02 step1；03 多种子平台白名单共用）。"""

from __future__ import annotations

import re
from typing import Dict, FrozenSet, Optional

# 用户消息中的平台提示（与 Java buildSeedJson 对齐）
PLATFORM_HINT = re.compile(
    r"(推特|twitter|\bx\b|微博|weibo|youtube|油管|bilibili|b站|哔哩哔哩|"
    r"instagram|ins|ig|tiktok|telegram|tg|facebook|fb|脸书|github)",
    re.I,
)

# platform → MCP 主页工具
MCP_SEED_PLATFORM_TOOLS: Dict[str, str] = {
    "twitter": "mcp_twitter_get_user_info",
    "weibo": "mcp_weibo_get_profile",
    "youtube": "mcp_youtube_get_channel_stats",
    "bilibili": "mcp_bilibili_get_user_info",
}

# platform → Apify Actor 启动工具（profile 实际来自 get_dataset_items）
APIFY_SEED_PLATFORM_TOOLS: Dict[str, str] = {
    "instagram": "mcp_apify_apify__instagram_scraper",
    "tiktok": "mcp_apify_clockworks__tiktok_scraper",
    "telegram": "mcp_apify_vujeen__telegram_channel_scraper",
    "facebook": "mcp_apify_headlessagent__facebook_profile_post_scraper",
    "github": "mcp_apify_knotless_cadence__github_profile_scraper",
}

# Apify 三轮链路上的非 Actor 工具
APIFY_SEED_FOLLOWUP_TOOLS: FrozenSet[str] = frozenset(
    {
        "mcp_apify_get_actor_run",
        "mcp_apify_get_dataset_items",
    }
)

# 兼容旧名：仅 MCP 主页工具集合（phases 等场景仍可能引用）
SEED_PLATFORM_TOOLS: Dict[str, str] = dict(MCP_SEED_PLATFORM_TOOLS)
SEED_PROFILE_TOOLS: FrozenSet[str] = frozenset(MCP_SEED_PLATFORM_TOOLS.values())

TOOL_TO_SEED_PLATFORM: Dict[str, str] = {
    **{v: k for k, v in MCP_SEED_PLATFORM_TOOLS.items()},
    **{v: k for k, v in APIFY_SEED_PLATFORM_TOOLS.items()},
}

PLATFORM_LABELS: Dict[str, str] = {
    "twitter": "Twitter",
    "weibo": "微博",
    "youtube": "YouTube",
    "bilibili": "B站",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "telegram": "Telegram",
    "facebook": "Facebook",
    "github": "GitHub",
}

# 01 采集 / 02 扩建 / 04 写报 step1：MCP + Apify Actor + 轮询/拉数
COLLECT_SEED_PROFILE_TOOLS: FrozenSet[str] = frozenset(
    set(MCP_SEED_PLATFORM_TOOLS.values())
    | set(APIFY_SEED_PLATFORM_TOOLS.values())
    | set(APIFY_SEED_FOLLOWUP_TOOLS)
)
EXPAND_SEED_PROFILE_TOOLS: FrozenSet[str] = COLLECT_SEED_PROFILE_TOOLS
REPORT_SEED_PROFILE_TOOLS: FrozenSet[str] = COLLECT_SEED_PROFILE_TOOLS

# 03 核查：多种子账号允许的平台（步骤一确认后，步骤二 MCP/Apify 采集）
VERIFY_SEED_PLATFORMS: FrozenSet[str] = frozenset(
    set(MCP_SEED_PLATFORM_TOOLS.keys()) | set(APIFY_SEED_PLATFORM_TOOLS.keys())
)


def parse_platform_from_message(user_message: str) -> str:
    """从用户一句话解析种子平台，默认 twitter。"""
    m = PLATFORM_HINT.search(user_message or "")
    if not m:
        return "twitter"
    token = m.group(1).lower()
    if token in {"微博", "weibo"}:
        return "weibo"
    if token in {"youtube", "油管"}:
        return "youtube"
    if token in {"bilibili", "b站", "哔哩哔哩"}:
        return "bilibili"
    if token in {"instagram", "ins", "ig"}:
        return "instagram"
    if token in {"tiktok"}:
        return "tiktok"
    if token in {"telegram", "tg"}:
        return "telegram"
    if token in {"facebook", "fb", "脸书"}:
        return "facebook"
    if token in {"github"}:
        return "github"
    return "twitter"


def is_apify_seed_platform(platform: str) -> bool:
    return (platform or "").lower() in APIFY_SEED_PLATFORM_TOOLS


def seed_tool_for_platform(platform: str) -> Optional[str]:
    plat = (platform or "twitter").lower()
    return MCP_SEED_PLATFORM_TOOLS.get(plat) or APIFY_SEED_PLATFORM_TOOLS.get(plat)


def seed_platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get((platform or "twitter").lower(), platform or "twitter")


def seed_step_completed_message(platform: str) -> str:
    return f"种子 {seed_platform_label(platform)} 资料已入库"


def apify_seed_empty_ok(tool_name: str) -> bool:
    """Actor 启动 / get_actor_run 成功时通常无 profile，不算种子失败。"""
    if tool_name in APIFY_SEED_PLATFORM_TOOLS.values():
        return True
    return tool_name == "mcp_apify_get_actor_run"
