"""03 账号核查 — 深度模式步骤定义。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

PHASE_INPUT_ACCOUNTS = "input_accounts"
PHASE_CROSS_PLATFORM = "cross_platform"
PHASE_STREAM_GEN = "stream_gen"
PHASE_STREAM_VALIDATE = "stream_validate"
PHASE_ACCOUNT_FINALIZE = "account_finalize"
PHASE_DONE = "done"

TASK_TYPE = "account_verify"
SKILL_NAME = "account-intelligence-verification"


@dataclass(frozen=True)
class StepDef:
    step_key: str
    title: str
    step_order: int
    step_node: str
    parent_step_key: Optional[str] = None
    current_phase: Optional[str] = None


ROOT_STEPS: Tuple[StepDef, ...] = (
    StepDef("step1_input_accounts", "步骤一：确认种子账号", 10, "1", None, PHASE_INPUT_ACCOUNTS),
    StepDef("step3_profiles", "步骤二：各平台主页与发文采集", 30, "2", None, PHASE_CROSS_PLATFORM),
    StepDef("step3_streams", "步骤三：发文风格与领域归纳", 40, "3", None, PHASE_STREAM_GEN),
    StepDef("step4_text_compare", "步骤四：文本流对比", 41, "4.1", None, PHASE_STREAM_VALIDATE),
    StepDef("step4_image_compare", "步骤四：图片流对比", 42, "4.2", None, PHASE_STREAM_VALIDATE),
    StepDef("step5_validated", "步骤五：账号核验结果", 50, "5", None, PHASE_ACCOUNT_FINALIZE),
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
}

# 主页 + 发文工具均归入 step3_profiles（无 step6 树节点）
TOOL_PRIMARY_STEP: Dict[str, str] = {
    "mcp_twitter_get_user_info": "step3_profiles",
    "mcp_youtube_get_channel_stats": "step3_profiles",
    "mcp_weibo_get_profile": "step3_profiles",
    "mcp_apify_apify__instagram_scraper": "step3_profiles",
    "mcp_apify_clockworks__tiktok_scraper": "step3_profiles",
    "mcp_apify_vujeen__telegram_channel_scraper": "step3_profiles",
    "mcp_apify_headlessagent__facebook_profile_post_scraper": "step3_profiles",
    "mcp_apify_knotless_cadence__github_profile_scraper": "step3_profiles",
    "mcp_apify_get_dataset_items": "step3_profiles",
    "mcp_apify_get_actor_run": "step3_profiles",
    "mcp_twitter_get_user_tweets": "step3_profiles",
    "mcp_youtube_analyze_channel_videos": "step3_profiles",
    "mcp_weibo_get_user_feeds": "step3_profiles",
    "mcp_weibo_get_feeds": "step3_profiles",
    "mcp_ocr_perform_ocr": "step4_image_compare",
    "mcp_vision_analyze": "step4_image_compare",
    "vision_analyze": "step4_image_compare",
}

PROFILE_TOOLS = frozenset(
    {
        "mcp_twitter_get_user_info",
        "mcp_youtube_get_channel_stats",
        "mcp_weibo_get_profile",
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
    **APIFY_TOOL_PLATFORM,
}

PROFILE_PARENT_STEP_KEY = "step3_profiles"

PLATFORM_STEP_INDEX: Dict[str, int] = {
    "twitter": 1,
    "facebook": 2,
    "youtube": 3,
    "instagram": 4,
    "telegram": 5,
    "weibo": 6,
    "tiktok": 7,
    "github": 8,
    "bilibili": 9,
}


def profile_platform_step_key(platform: str) -> str:
    return f"step3_profile_{platform}"


def post_platform_step_key(platform: str) -> str:
    return f"step3_post_{platform}"


def profile_step_title(platform: str) -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    return f"{label} 主页采集"


def post_step_title(platform: str) -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    return f"{label} 发文采集"


def profile_step_order(platform: str) -> int:
    return 300 + PLATFORM_STEP_INDEX.get(platform, 90)


def post_step_order(platform: str) -> int:
    return 350 + PLATFORM_STEP_INDEX.get(platform, 90)


def profile_step_node(platform: str) -> str:
    idx = PLATFORM_STEP_INDEX.get(platform, 90)
    return f"2.{idx}"


def post_step_node(platform: str) -> str:
    idx = PLATFORM_STEP_INDEX.get(platform, 90)
    return f"2.{idx}.1"


def is_profile_platform_step(step_key: Optional[str]) -> bool:
    if not step_key:
        return False
    s = str(step_key)
    if s == PROFILE_PARENT_STEP_KEY:
        return False
    return s.startswith("step3_profile_")


def is_post_platform_step(step_key: Optional[str]) -> bool:
    return bool(step_key) and str(step_key).startswith("step3_post_")


def platform_collect_step_key(platform: str, *, kind: str) -> str:
    if kind == "post":
        return post_platform_step_key(platform)
    return profile_platform_step_key(platform)


def tool_collect_step_key(tool_name: str, platform: Optional[str]) -> str:
    """工具落库 phase / 展示步骤：区分主页与发文子节点。"""
    if not platform:
        return "step3_profiles"
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
    return TOOL_PRIMARY_STEP.get(tool_name, "step3_profiles")


def is_step_key(value: Optional[str]) -> bool:
    return bool(value) and str(value).startswith("step")


def step_phase(step_key: str) -> Optional[str]:
    for s in ROOT_STEPS:
        if s.step_key == step_key:
            return s.current_phase
    if is_profile_platform_step(step_key) or is_post_platform_step(step_key):
        return PHASE_CROSS_PLATFORM
    return None
