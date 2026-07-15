"""04 账号画像写报 — 深度模式 11 步定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from collect_01.seed_platforms import REPORT_SEED_PROFILE_TOOLS as SEED_PROFILE_TOOLS

PHASE_SEED = "seed"
PHASE_DISCOVERY = "discovery"
PHASE_PROFILES = "profiles"
PHASE_STREAM_VALIDATE = "stream_validate"
PHASE_VALIDATED = "validated"
PHASE_POSTS = "posts"
PHASE_ANALYSIS = "analysis"
PHASE_REPORT = "report"
PHASE_DONE = "done"

TASK_TYPE = "account_report"
SKILL_NAME = "account-intelligence-report"

ANALYSIS_STEP_KEYS = (
    "step8_img_analysis",
    "step9_context_views",
    "step10_context_pii",
)

PROFILE_PARENT_STEP_KEY = "step4_profiles"
POST_PARENT_STEP_KEY = "step7_posts"


@dataclass(frozen=True)
class StepDef:
    step_key: str
    title: str
    step_order: int
    step_node: str
    parent_step_key: Optional[str] = None
    current_phase: Optional[str] = None


ROOT_STEPS: Tuple[StepDef, ...] = (
    StepDef("step1_seed", "步骤一：种子 profile", 10, "1", None, PHASE_SEED),
    StepDef("step2_maigret", "步骤二：Maigret 跨平台发现", 20, "2", None, PHASE_DISCOVERY),
    StepDef("step3_web_search", "步骤三：网页检索候选", 30, "3", None, PHASE_DISCOVERY),
    StepDef("step4_profiles", "步骤四：候选主页采集", 40, "4", None, PHASE_PROFILES),
    StepDef("step5_streams", "步骤五：文本/图片流核查", 50, "5", None, PHASE_STREAM_VALIDATE),
    StepDef("step6_validated", "步骤六：相似账号认定", 60, "6", None, PHASE_VALIDATED),
    StepDef("step7_posts", "步骤七：发文采集", 70, "7", None, PHASE_POSTS),
    StepDef("step8_img_analysis", "步骤八：图片流分析", 80, "8", None, PHASE_ANALYSIS),
    StepDef("step9_context_views", "步骤九：观点与涉华分析", 90, "9", None, PHASE_ANALYSIS),
    StepDef("step10_context_pii", "步骤十：PII 与圈层分析", 100, "10", None, PHASE_ANALYSIS),
    StepDef("step11_report", "步骤十一：画像报告", 110, "11", None, PHASE_REPORT),
)

PLATFORM_LABELS: Dict[str, str] = {
    "twitter": "Twitter",
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
    "telegram": "Telegram",
    "weibo": "微博",
    "bilibili": "B站",
    "facebook": "Facebook",
    "github": "GitHub",
    "reddit": "Reddit",
    "linkedin": "LinkedIn",
    "vk": "VK",
}

PLATFORM_STEP_INDEX: Dict[str, int] = {
    "twitter": 1,
    "facebook": 2,
    "youtube": 3,
    "instagram": 4,
    "telegram": 5,
    "weibo": 6,
    "tiktok": 7,
    "github": 8,
    "reddit": 9,
    "linkedin": 10,
    "vk": 11,
    "bilibili": 12,
}

TOOL_PRIMARY_STEP: Dict[str, str] = {
    "mcp_twitter_get_user_info": "step1_seed",
    "mcp_youtube_get_channel_stats": "step1_seed",
    "mcp_weibo_get_profile": "step1_seed",
    "mcp_bilibili_get_user_info": "step1_seed",
    "mcp_maigret_collect_accounts": "step2_maigret",
    "web_search": "step3_web_search",
    "web_extract": "step3_web_search",
    "browser_navigate": "step3_web_search",
    "browser_vision": "step3_web_search",
    "browser_click": "step3_web_search",
    "browser_type": "step3_web_search",
    "mcp_apify_apify__instagram_scraper": "step4_profiles",
    "mcp_apify_clockworks__tiktok_scraper": "step4_profiles",
    "mcp_apify_vujeen__telegram_channel_scraper": "step4_profiles",
    "mcp_apify_headlessagent__facebook_profile_post_scraper": "step4_profiles",
    "mcp_apify_knotless_cadence__github_profile_scraper": "step4_profiles",
    "mcp_apify_get_dataset_items": "step4_profiles",
    "mcp_apify_get_actor_run": "step4_profiles",
    "mcp_ocr_perform_ocr": "step5_streams",
    "mcp_vision_analyze": "step5_streams",
    "vision_analyze": "step5_streams",
    "mcp_twitter_get_user_tweets": "step7_posts",
    "mcp_youtube_analyze_channel_videos": "step7_posts",
    "mcp_weibo_get_user_feeds": "step7_posts",
    "mcp_weibo_get_feeds": "step7_posts",
}

PROFILE_TOOLS = frozenset(
    {
        "mcp_twitter_get_user_info",
        "mcp_youtube_get_channel_stats",
        "mcp_weibo_get_profile",
        "mcp_bilibili_get_user_info",
        "mcp_apify_apify__instagram_scraper",
        "mcp_apify_clockworks__tiktok_scraper",
        "mcp_apify_vujeen__telegram_channel_scraper",
        "mcp_apify_headlessagent__facebook_profile_post_scraper",
        "mcp_apify_knotless_cadence__github_profile_scraper",
        "mcp_apify_get_actor_run",
    }
)

POST_TOOLS = frozenset(
    {
        "mcp_twitter_get_user_tweets",
        "mcp_youtube_analyze_channel_videos",
        "mcp_weibo_get_user_feeds",
        "mcp_weibo_get_feeds",
        "mcp_apify_get_dataset_items",
    }
)

STEP5_STREAM_TOOLS = frozenset({"mcp_ocr_perform_ocr", "mcp_vision_analyze", "vision_analyze"})

STEP8_VISION_TOOLS = frozenset({"mcp_vision_analyze", "vision_analyze", "mcp_ocr_perform_ocr"})

WEB_SEARCH_TOOLS = frozenset(
    {
        "web_search",
        "web_extract",
        "browser_navigate",
        "browser_vision",
        "browser_click",
        "browser_type",
    }
)

APIFY_POST_TOOLS = {
    "mcp_apify_apify__instagram_scraper",
    "mcp_apify_clockworks__tiktok_scraper",
    "mcp_apify_vujeen__telegram_channel_scraper",
    "mcp_apify_headlessagent__facebook_profile_post_scraper",
    "mcp_apify_knotless_cadence__github_profile_scraper",
}

STEP4_COLLECT_TOOLS = PROFILE_TOOLS | POST_TOOLS | APIFY_POST_TOOLS | frozenset(
    {"mcp_apify_get_actor_run", "mcp_apify_get_dataset_items"}
)

APIFY_TOOL_PLATFORM: Dict[str, str] = {
    "mcp_apify_apify__instagram_scraper": "instagram",
    "mcp_apify_clockworks__tiktok_scraper": "tiktok",
    "mcp_apify_vujeen__telegram_channel_scraper": "telegram",
    "mcp_apify_headlessagent__facebook_profile_post_scraper": "facebook",
    "mcp_apify_knotless_cadence__github_profile_scraper": "github",
}

TOOL_PLATFORM: Dict[str, str] = {
    "mcp_twitter_get_user_info": "twitter",
    "mcp_twitter_get_user_tweets": "twitter",
    "mcp_youtube_get_channel_stats": "youtube",
    "mcp_youtube_analyze_channel_videos": "youtube",
    "mcp_weibo_get_profile": "weibo",
    "mcp_weibo_get_user_feeds": "weibo",
    "mcp_weibo_get_feeds": "weibo",
    "mcp_bilibili_get_user_info": "bilibili",
    **APIFY_TOOL_PLATFORM,
}

TOOL_POST_PLATFORM: Dict[str, str] = {
    "mcp_twitter_get_user_tweets": "twitter",
    "mcp_youtube_analyze_channel_videos": "youtube",
    "mcp_weibo_get_user_feeds": "weibo",
    "mcp_weibo_get_feeds": "weibo",
}

# Maigret 常返回但写报无 MCP/Apify 采集通道的平台
NON_COLLECTIBLE_PLATFORMS = frozenset(
    {
        "imginn",
        "picuki",
        "googlescholar",
        "blogger",
        "discord",
        "snapchat",
        "steam",
        "wordpress",
        "opggloltaiwan",
        "naver",
        "slack",
        "twitch",
        "vk",
        "reddit",
        "linkedin",
        "pinterest",
        "medium",
        "tumblr",
    }
)

_COLLECTIBLE_PLATFORM_SET = frozenset(set(TOOL_PLATFORM.values()) | set(APIFY_TOOL_PLATFORM.values()))


def is_collectible_platform(platform: str) -> bool:
    p = (platform or "").strip().lower()
    if not p or p in NON_COLLECTIBLE_PLATFORMS:
        return False
    return p in _COLLECTIBLE_PLATFORM_SET


def profile_platform_step_key(platform: str) -> str:
    return f"step4_profile_{platform}"


def post_platform_step_key(platform: str) -> str:
    return f"step7_post_{platform}"


def profile_step_title(platform: str, handle: str = "") -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    if handle:
        return f"{label} 主页采集 · @{handle.lstrip('@')}"
    return f"{label} 主页采集"


def post_step_title(platform: str, handle: str = "") -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    if handle:
        return f"{label} 发文采集 · @{handle.lstrip('@')}"
    return f"{label} 发文采集"


def profile_step_order(platform: str) -> int:
    return 400 + PLATFORM_STEP_INDEX.get(platform, 90)


def post_step_order(platform: str) -> int:
    return 700 + PLATFORM_STEP_INDEX.get(platform, 90)


def profile_step_node(platform: str) -> str:
    idx = PLATFORM_STEP_INDEX.get(platform, 90)
    return f"4.{idx}"


def post_step_node(platform: str) -> str:
    idx = PLATFORM_STEP_INDEX.get(platform, 90)
    return f"7.{idx}"


def is_profile_platform_step(step_key: Optional[str]) -> bool:
    return bool(step_key) and str(step_key).startswith("step4_profile_")


def is_post_platform_step(step_key: Optional[str]) -> bool:
    return bool(step_key) and str(step_key).startswith("step7_post_")


def tool_collect_step_key(tool_name: str, platform: Optional[str]) -> str:
    if not platform:
        return TOOL_PRIMARY_STEP.get(tool_name, "step4_profiles")
    if tool_name in POST_TOOLS:
        return post_platform_step_key(platform)
    if tool_name in PROFILE_TOOLS:
        return profile_platform_step_key(platform)
    return profile_platform_step_key(platform)


def root_step_keys() -> List[str]:
    return [s.step_key for s in ROOT_STEPS]


def initial_steps() -> List[StepDef]:
    return list(ROOT_STEPS)


def tool_step_key(tool_name: str) -> str:
    return TOOL_PRIMARY_STEP.get(tool_name, "step4_profiles")


def step_phase(step_key: str) -> Optional[str]:
    for s in ROOT_STEPS:
        if s.step_key == step_key:
            return s.current_phase
    if is_profile_platform_step(step_key):
        return PHASE_PROFILES
    if is_post_platform_step(step_key):
        return PHASE_POSTS
    return None
