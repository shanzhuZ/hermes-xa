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

# 走独立 MCP 的种子平台（其余走 Apify Agent）
_MCP_SEED_PLATFORMS = frozenset({"twitter", "weibo", "youtube", "bilibili"})


@dataclass(frozen=True)
class StepDef:
    step_key: str
    title: str
    step_order: int
    step_node: str
    parent_step_key: Optional[str] = None
    current_phase: Optional[str] = None


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


def seed_agent_title(platform: Optional[str]) -> str:
    """步骤一展示名：按种子平台显示 MCP / Apify Agent。"""
    plat = (platform or "twitter").lower().strip()
    label = PLATFORM_LABELS.get(plat, plat)
    if plat in _MCP_SEED_PLATFORMS:
        return f"{label} MCP Agent 采集"
    return f"Apify Agent · {label} 采集"


def root_steps_for_platform(platform: Optional[str] = None) -> Tuple[StepDef, ...]:
    """完整步骤树（步骤六子节点另按平台动态追加）。"""
    return (
        StepDef("step1_seed", seed_agent_title(platform), 10, "1", None, PHASE_RESOLVE_SEED),
        StepDef("step2_cross_platform", "Maigret Agent 跨平台收集", 20, "2", None, PHASE_CROSS_PLATFORM),
        StepDef("step3_profiles", "MCP/Apify Agent 候选主页采集", 30, "3", None, PHASE_CROSS_PLATFORM),
        StepDef("step3_streams", "信息核验流 Agent 拆分", 40, "4", None, PHASE_STREAM_GEN),
        StepDef("step4_text_compare", "文本流 Agent 对比", 41, "4.1", None, PHASE_STREAM_VALIDATE),
        StepDef("step4_image_compare", "图片流 Agent 分析", 42, "4.2", None, PHASE_STREAM_VALIDATE),
        StepDef("step5_validated", "可信账号核验 Agent", 50, "5", None, PHASE_ACCOUNT_FINALIZE),
        StepDef("step6_posts", "跨平台发文采集 Agent", 60, "6", None, PHASE_COLLECT),
    )


# 兼容旧引用：默认按 twitter 种子生成标题
ROOT_STEPS: Tuple[StepDef, ...] = root_steps_for_platform("twitter")

# 工具 → 触发的步骤（用于自动更新 running/completed）
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
    "mcp_apify_get_dataset_items": "step3_profiles",
    "mcp_ocr_perform_ocr": "step4_image_compare",
    "mcp_vision_analyze": "step4_image_compare",
    "vision_analyze": "step4_image_compare",
    "mcp_twitter_get_user_tweets": "step6_posts",
    "mcp_youtube_analyze_channel_videos": "step6_posts",
    "mcp_weibo_get_user_feeds": "step6_posts",
    "mcp_weibo_get_feeds": "step6_posts",
}

# 发文类工具 → 平台（用于挂到 step6_post_{platform} 子步骤）
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
    return f"{label} Agent 发文采集"


def post_step_index(platform: str) -> int:
    return PLATFORM_STEP_INDEX.get(platform, 90)


def post_step_order(platform: str) -> int:
    return 600 + post_step_index(platform)


def post_step_node(platform: str) -> str:
    return f"6.{post_step_index(platform)}"


def root_step_keys() -> List[str]:
    return [s.step_key for s in ROOT_STEPS]


def initial_steps(cross_platform: bool, platform: Optional[str] = None) -> List[StepDef]:
    steps = root_steps_for_platform(platform)
    if cross_platform:
        return list(steps)
    return [s for s in steps if s.step_key in {"step1_seed", "step6_posts"}]


def tool_step_key(tool_name: str) -> str:
    """工具调用归属的步骤键，与 collect_phase_steps.step_key 一致。"""
    platform = TOOL_POST_PLATFORM.get(tool_name)
    if platform:
        return post_step_key(platform)
    return TOOL_PRIMARY_STEP.get(tool_name, "step6_posts")


def is_step_key(value: Optional[str]) -> bool:
    """判断 phase 字段是否已是步骤键（step1_seed / step6_post_twitter 等）。"""
    return bool(value) and str(value).startswith("step")


def step_phase(step_key: str) -> Optional[str]:
    for s in ROOT_STEPS:
        if s.step_key == step_key:
            return s.current_phase
    if step_key.startswith("step6_post_"):
        return PHASE_COLLECT
    return None
