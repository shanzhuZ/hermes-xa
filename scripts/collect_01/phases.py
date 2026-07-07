"""01 采集 — 前端深度模式步骤定义（密塔式树形进度）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Hermes 任务大阶段（hermes_tasks.current_phase）
PHASE_RESOLVE_SEED = "resolve_seed"
PHASE_CROSS_PLATFORM = "cross_platform"
PHASE_STREAM_GEN = "stream_gen"
PHASE_STREAM_VALIDATE = "stream_validate"
PHASE_ACCOUNT_FINALIZE = "account_finalize"
PHASE_COLLECT = "collect"
PHASE_DONE = "done"

TASK_TYPE = "account_collect"


@dataclass(frozen=True)
class StepDef:
    step_key: str
    title: str
    step_order: int
    parent_step_key: Optional[str] = None
    current_phase: Optional[str] = None


# 固定步骤树（步骤六的子步骤按平台动态追加）
ROOT_STEPS: Tuple[StepDef, ...] = (
    StepDef("step1_seed", "步骤一：种子账号资料采集", 10, None, PHASE_RESOLVE_SEED),
    StepDef("step2_cross_platform", "步骤二：跨平台账号收集", 20, None, PHASE_CROSS_PLATFORM),
    StepDef("step3_profiles", "步骤三：候选主页采集", 30, None, PHASE_CROSS_PLATFORM),
    StepDef("step3_streams", "步骤四：文本流与图片流拆分", 40, None, PHASE_STREAM_GEN),
    StepDef("step4_text_compare", "步骤四：文本流对比", 41, None, PHASE_STREAM_VALIDATE),
    StepDef("step4_image_compare", "步骤四：图片流对比", 42, None, PHASE_STREAM_VALIDATE),
    StepDef("step5_validated", "步骤五：可信账号收敛", 50, None, PHASE_ACCOUNT_FINALIZE),
    StepDef("step6_posts", "步骤六：分平台发文采集", 60, None, PHASE_COLLECT),
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

# 工具 → 触发的步骤（用于自动更新 running/completed）
TOOL_PRIMARY_STEP: Dict[str, str] = {
    "mcp_twitter_get_user_info": "step1_seed",
    "mcp_maigret_collect_accounts": "step2_cross_platform",
    "mcp_youtube_get_channel_stats": "step3_profiles",
    "mcp_weibo_get_profile": "step3_profiles",
    "mcp_apify_apify__instagram_scraper": "step3_profiles",
    "mcp_apify_clockworks__tiktok_scraper": "step3_profiles",
    "mcp_apify_vujeen__telegram_channel_scraper": "step3_profiles",
    "mcp_apify_get_dataset_items": "step3_profiles",
    "mcp_ocr_perform_ocr": "step4_image_compare",
    "mcp_vision_analyze": "step4_image_compare",
    "mcp_twitter_get_user_tweets": "step6_posts",
    "mcp_youtube_analyze_channel_videos": "step6_posts",
    "mcp_weibo_get_user_feeds": "step6_posts",
}

# 发文类工具 → 平台（用于挂到 step6_post_{platform} 子步骤）
TOOL_POST_PLATFORM: Dict[str, str] = {
    "mcp_twitter_get_user_tweets": "twitter",
    "mcp_youtube_analyze_channel_videos": "youtube",
    "mcp_weibo_get_user_feeds": "weibo",
}

APIFY_POST_TOOLS = {
    "mcp_apify_apify__instagram_scraper",
    "mcp_apify_clockworks__tiktok_scraper",
    "mcp_apify_vujeen__telegram_channel_scraper",
}


def post_step_key(platform: str) -> str:
    return f"step6_post_{platform}"


def post_step_title(platform: str) -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    return f"{label} 发文采集"


def root_step_keys() -> List[str]:
    return [s.step_key for s in ROOT_STEPS]


def step_phase(step_key: str) -> Optional[str]:
    for s in ROOT_STEPS:
        if s.step_key == step_key:
            return s.current_phase
    if step_key.startswith("step6_post_"):
        return PHASE_COLLECT
    return None
