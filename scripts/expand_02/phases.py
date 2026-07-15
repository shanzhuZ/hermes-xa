"""02 账号扩建 — 深度模式步骤定义（共用 step_key，标题按四段式 UI）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# 与 01 共用大阶段枚举
PHASE_RESOLVE_SEED = "resolve_seed"
PHASE_CROSS_PLATFORM = "cross_platform"
PHASE_STREAM_GEN = "stream_gen"
PHASE_STREAM_VALIDATE = "stream_validate"
PHASE_ACCOUNT_FINALIZE = "account_finalize"
PHASE_COLLECT = "collect"
PHASE_DONE = "done"

TASK_TYPE = "account_expand"
SKILL_NAME = "account-expansion"


@dataclass(frozen=True)
class StepDef:
    step_key: str
    title: str
    step_order: int
    step_node: str
    parent_step_key: Optional[str] = None
    current_phase: Optional[str] = None


# 共用 step_key；标题对应扩建四节输出语义
POST_PARENT_STEP_KEY = "step3_profiles"

ROOT_STEPS: Tuple[StepDef, ...] = (
    StepDef("step1_seed", "一、账号扩建收集：种子资料", 10, "1", None, PHASE_RESOLVE_SEED),
    StepDef("step2_cross_platform", "一、账号扩建收集：跨平台发现", 20, "1.2", None, PHASE_CROSS_PLATFORM),
    StepDef("step3_profiles", "二、多平台信息采集：主页与发文", 30, "2", None, PHASE_CROSS_PLATFORM),
    StepDef("step3_streams", "三、账号核查：流拆分", 40, "3", None, PHASE_STREAM_GEN),
    StepDef("step4_text_compare", "三、账号核查：文本流对比", 41, "3.1", None, PHASE_STREAM_VALIDATE),
    StepDef("step4_image_compare", "三、账号核查：图片流对比", 42, "3.2", None, PHASE_STREAM_VALIDATE),
    StepDef("step5_validated", "四、可信账号输出", 50, "4", None, PHASE_ACCOUNT_FINALIZE),
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

PLATFORM_STEP_INDEX: Dict[str, int] = {
    "twitter": 1,
    "youtube": 2,
    "weibo": 3,
    "instagram": 4,
    "tiktok": 5,
    "telegram": 6,
    "facebook": 7,
    "github": 8,
    "bilibili": 9,
}

# 工具 → 步骤（与 01 对齐，扩建步骤3 同时采 profile+发文）
TOOL_PRIMARY_STEP: Dict[str, str] = {
    "mcp_twitter_get_user_info": "step1_seed",
    "mcp_maigret_collect_accounts": "step2_cross_platform",
    "mcp_youtube_get_channel_stats": "step3_profiles",
    "mcp_weibo_get_profile": "step3_profiles",
    "mcp_bilibili_get_user_info": "step3_profiles",
    "mcp_apify_apify__instagram_scraper": "step3_profiles",
    "mcp_apify_clockworks__tiktok_scraper": "step3_profiles",
    "mcp_apify_vujeen__telegram_channel_scraper": "step3_profiles",
    "mcp_apify_headlessagent__facebook_profile_post_scraper": "step3_profiles",
    "mcp_apify_knotless_cadence__github_profile_scraper": "step3_profiles",
    "mcp_apify_get_actor_run": "step3_profiles",
    "mcp_apify_get_dataset_items": "step3_profiles",
    "mcp_ocr_perform_ocr": "step4_image_compare",
    "mcp_vision_analyze": "step4_image_compare",
    "vision_analyze": "step4_image_compare",
    "mcp_twitter_get_user_tweets": "step3_profiles",
    "mcp_youtube_analyze_channel_videos": "step3_profiles",
    "mcp_weibo_get_user_feeds": "step3_profiles",
    "mcp_weibo_get_feeds": "step3_profiles",
}

TOOL_POST_PLATFORM: Dict[str, str] = {
    "mcp_twitter_get_user_tweets": "twitter",
    "mcp_youtube_analyze_channel_videos": "youtube",
    "mcp_weibo_get_user_feeds": "weibo",
    "mcp_weibo_get_feeds": "weibo",
}

APIFY_POST_TOOLS = {
    "mcp_apify_apify__instagram_scraper",
    "mcp_apify_clockworks__tiktok_scraper",
    "mcp_apify_vujeen__telegram_channel_scraper",
    "mcp_apify_headlessagent__facebook_profile_post_scraper",
    "mcp_apify_knotless_cadence__github_profile_scraper",
}

APIFY_TOOL_PLATFORM: Dict[str, str] = {
    "mcp_apify_apify__instagram_scraper": "instagram",
    "mcp_apify_clockworks__tiktok_scraper": "tiktok",
    "mcp_apify_vujeen__telegram_channel_scraper": "telegram",
    "mcp_apify_headlessagent__facebook_profile_post_scraper": "facebook",
    "mcp_apify_knotless_cadence__github_profile_scraper": "github",
}


def post_step_key(platform: str) -> str:
    return f"step6_post_{platform}"


def post_step_title(platform: str) -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    return f"{label} 发文采集"


def post_step_index(platform: str) -> int:
    return PLATFORM_STEP_INDEX.get(platform, 90)


def post_step_order(platform: str) -> int:
    return 300 + post_step_index(platform)


def post_step_node(platform: str) -> str:
    return f"2.{post_step_index(platform)}"


def root_step_keys() -> List[str]:
    return [s.step_key for s in ROOT_STEPS]


def initial_steps(_cross_platform: bool = True) -> List[StepDef]:
    """扩建默认始终跨平台，插入完整步骤树。"""
    return list(ROOT_STEPS)


def tool_step_key(tool_name: str) -> str:
    platform = TOOL_POST_PLATFORM.get(tool_name)
    if platform:
        return post_step_key(platform)
    return TOOL_PRIMARY_STEP.get(tool_name, "step3_profiles")


def is_step_key(value: Optional[str]) -> bool:
    return bool(value) and str(value).startswith("step")


def step_phase(step_key: str) -> Optional[str]:
    for s in ROOT_STEPS:
        if s.step_key == step_key:
            return s.current_phase
    if step_key.startswith("step6_post_"):
        return PHASE_COLLECT
    return None
