"""04 账号画像写报 — 七大阶段壳 + 业务子步定义。

图片资产管线仍在步骤7发文收口后后台跑（见 orchestrator/image_assets），不进步骤树。
"""

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

STEP_PLAN_KEY = "step_plan"

PHASE_LOCK_TARGET = "phase_lock_target"
PHASE_DISCOVERY_SHELL = "phase_discovery"
PHASE_ACCOUNT_COLLECT = "phase_account_collect"
PHASE_COLLISION = "phase_collision"
PHASE_CONTENT = "phase_content"
PHASE_ANALYSIS_SHELL = "phase_analysis"
PHASE_REPORT_SHELL = "phase_report"

_MCP_SEED_PLATFORMS = frozenset({"twitter", "weibo", "youtube", "bilibili"})

ANALYSIS_STEP_KEYS = (
    "step8_img_analysis",
    "step9_context_views",
    "step10_context_pii",
)

PROFILE_PARENT_STEP_KEY = "step4_profiles"
POST_PARENT_STEP_KEY = "step7_posts"
STREAM_PARENT_STEP_KEY = "step5_streams"
STREAM_TEXT_STEP_KEY = "step5_stream_text"
STREAM_IMAGE_STEP_KEY = "step5_stream_image"

# Agent 文本核验结论标记（Hook 解析）
STREAM_TEXT_CONCLUSION_MARKER = "[文本核验结论]"
# Agent 社工库核验结论标记（Hook 解析）
OSINT_ES_STEP_KEY = "step6_osint_es"
OSINT_CONCLUSION_MARKER = "[社工库核验结论]"

# ---------------------------------------------------------------------------
# [COLLISION_DEMO_FAKE] 假流程节点（演示用）— 正式版请整段删除
# 文档约定：挂在「4. 关联碰撞」下；不挡发文门禁；壳收口不等它们。
# 实现：collision_demo_steps.py ；触发：phase_collision → running
# 检索关键字：COLLISION_DEMO_FAKE
# ---------------------------------------------------------------------------
STEP6_GEO_VERIFY = "step6_geo_verify"  # 4.4 地理位置核验 Agent
STEP6_RELATION_GRAPH = "step6_relation_graph"  # 4.5 关系网络分析 Agent
STEP6_RUMOR_SX = "step6_rumor_sx"  # 4.6 陕西谣言特色库 Agent
COLLISION_DEMO_STEP_KEYS = frozenset(
    {STEP6_GEO_VERIFY, STEP6_RELATION_GRAPH, STEP6_RUMOR_SX}
)
# ---------------------------------------------------------------------------


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


def seed_agent_title(platform: Optional[str]) -> str:
    """步骤一种子采集展示名（与 01/02 一致）。"""
    plat = (platform or "twitter").lower().strip()
    label = PLATFORM_LABELS.get(plat, plat)
    if plat in _MCP_SEED_PLATFORMS:
        return f"{label} MCP Agent 采集"
    return f"Apify Agent · {label} 采集"


def phase_shell_steps() -> Tuple[StepDef, ...]:
    """L1 七大阶段壳（挂在 step_plan 下；图片资产管线不进树）。"""
    return (
        StepDef(PHASE_LOCK_TARGET, "1. 锁定目标", 100, "1", STEP_PLAN_KEY, PHASE_SEED),
        StepDef(PHASE_DISCOVERY_SHELL, "2. 线索发现", 200, "2", STEP_PLAN_KEY, PHASE_DISCOVERY),
        StepDef(PHASE_ACCOUNT_COLLECT, "3. 账号采集", 300, "3", STEP_PLAN_KEY, PHASE_PROFILES),
        StepDef(PHASE_COLLISION, "4. 关联碰撞", 400, "4", STEP_PLAN_KEY, PHASE_STREAM_VALIDATE),
        StepDef(PHASE_CONTENT, "5. 内容采集", 500, "5", STEP_PLAN_KEY, PHASE_POSTS),
        StepDef(PHASE_ANALYSIS_SHELL, "6. 深度研判", 600, "6", STEP_PLAN_KEY, PHASE_ANALYSIS),
        StepDef(PHASE_REPORT_SHELL, "7. 报告生成", 700, "7", STEP_PLAN_KEY, PHASE_REPORT),
    )


def execution_steps_for_platform(platform: Optional[str] = None) -> Tuple[StepDef, ...]:
    """业务执行步（挂在七大壳下；step_key 不变）。"""
    return (
        StepDef("step1_seed", seed_agent_title(platform), 110, "1.1", PHASE_LOCK_TARGET, PHASE_SEED),
        StepDef("step2_maigret", "Maigret Agent 跨平台收集", 210, "2.1", PHASE_DISCOVERY_SHELL, PHASE_DISCOVERY),
        StepDef("step3_web_search", "网页检索 Agent 候选发现", 220, "2.2", PHASE_DISCOVERY_SHELL, PHASE_DISCOVERY),
        StepDef(
            "step4_profiles",
            "MCP/Apify Agent 候选主页采集",
            310,
            "3.1",
            PHASE_ACCOUNT_COLLECT,
            PHASE_PROFILES,
        ),
        StepDef("step5_streams", "信息核验流 Agent 核查", 410, "4.1", PHASE_COLLISION, PHASE_STREAM_VALIDATE),
        StepDef(
            STREAM_TEXT_STEP_KEY,
            "文本流核验 Agent",
            411,
            "4.1.1",
            STREAM_PARENT_STEP_KEY,
            PHASE_STREAM_VALIDATE,
        ),
        StepDef(
            STREAM_IMAGE_STEP_KEY,
            "图片流核验 Agent",
            412,
            "4.1.2",
            STREAM_PARENT_STEP_KEY,
            PHASE_STREAM_VALIDATE,
        ),
        StepDef("step6_validated", "相似账号认定 Agent", 420, "4.2", PHASE_COLLISION, PHASE_VALIDATED),
        StepDef(
            OSINT_ES_STEP_KEY,
            "社工库核验 Agent",
            430,
            "4.3",
            PHASE_COLLISION,
            PHASE_VALIDATED,
        ),
        # [COLLISION_DEMO_FAKE] 开始 — 正式版删除下列三步
        StepDef(
            STEP6_GEO_VERIFY,
            "地理位置核验 Agent",
            440,
            "4.4",
            PHASE_COLLISION,
            PHASE_STREAM_VALIDATE,
        ),
        StepDef(
            STEP6_RELATION_GRAPH,
            "关系网络分析 Agent",
            450,
            "4.5",
            PHASE_COLLISION,
            PHASE_STREAM_VALIDATE,
        ),
        StepDef(
            STEP6_RUMOR_SX,
            "陕西谣言特色库 Agent",
            460,
            "4.6",
            PHASE_COLLISION,
            PHASE_STREAM_VALIDATE,
        ),
        # [COLLISION_DEMO_FAKE] 结束
        StepDef("step7_posts", "跨平台发文采集 Agent", 510, "5.1", PHASE_CONTENT, PHASE_POSTS),
        StepDef("step8_img_analysis", "图片流 Agent 分析", 610, "6.1", PHASE_ANALYSIS_SHELL, PHASE_ANALYSIS),
        StepDef("step9_context_views", "观点与涉华分析 Agent", 620, "6.2", PHASE_ANALYSIS_SHELL, PHASE_ANALYSIS),
        StepDef("step10_context_pii", "PII 与圈层分析 Agent", 630, "6.3", PHASE_ANALYSIS_SHELL, PHASE_ANALYSIS),
        StepDef("step11_report", "画像报告 Agent", 710, "7.1", PHASE_REPORT_SHELL, PHASE_REPORT),
    )


def root_steps_for_platform(platform: Optional[str] = None) -> Tuple[StepDef, ...]:
    """完整预插树：规划 + 七壳 + 业务子步（平台动态子节点另追加）。"""
    plan = StepDef(STEP_PLAN_KEY, "制定执行计划", 5, "0", None, None)
    return (plan,) + phase_shell_steps() + execution_steps_for_platform(platform)


# 兼容旧引用：默认按 twitter 种子生成标题
ROOT_STEPS: Tuple[StepDef, ...] = root_steps_for_platform("twitter")

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
    # 社工库（Hermes 可能 sanitize 为 es_search）
    "mcp_es_search_search_country_wise": OSINT_ES_STEP_KEY,
    "mcp_es-search_search_country_wise": OSINT_ES_STEP_KEY,
    "mcp_es_search_list_es_indices": OSINT_ES_STEP_KEY,
    "mcp_es_search_es_cluster_health": OSINT_ES_STEP_KEY,
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
        return f"{label} Agent 主页采集 · @{handle.lstrip('@')}"
    return f"{label} Agent 主页采集"


def post_step_title(platform: str, handle: str = "") -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    if handle:
        return f"{label} Agent 发文采集 · @{handle.lstrip('@')}"
    return f"{label} Agent 发文采集"


def profile_step_order(platform: str) -> int:
    return 310 + PLATFORM_STEP_INDEX.get(platform, 90)


def post_step_order(platform: str) -> int:
    return 510 + PLATFORM_STEP_INDEX.get(platform, 90)


def profile_step_node(platform: str) -> str:
    idx = PLATFORM_STEP_INDEX.get(platform, 90)
    return f"3.1.{idx}"


def post_step_node(platform: str) -> str:
    idx = PLATFORM_STEP_INDEX.get(platform, 90)
    return f"5.1.{idx}"


def video_step_key(platform: str) -> str:
    """发文平台视频子节点：5.1.x.1 → step7_video_{platform}。"""
    return f"step7_video_{platform}"


def video_step_title(platform: str) -> str:
    label = PLATFORM_LABELS.get(platform, platform)
    return f"{label}视频分析"


def video_step_order(platform: str) -> int:
    return post_step_order(platform) + 1


def video_step_node(platform: str) -> str:
    return f"{post_step_node(platform)}.1"


def is_profile_platform_step(step_key: Optional[str]) -> bool:
    return bool(step_key) and str(step_key).startswith("step4_profile_")


def is_post_platform_step(step_key: Optional[str]) -> bool:
    return bool(step_key) and str(step_key).startswith("step7_post_")


def is_video_platform_step(step_key: Optional[str]) -> bool:
    return bool(step_key) and str(step_key).startswith("step7_video_")


def tool_collect_step_key(tool_name: str, platform: Optional[str]) -> str:
    if not platform:
        return TOOL_PRIMARY_STEP.get(tool_name, "step4_profiles")
    if tool_name in POST_TOOLS:
        return post_platform_step_key(platform)
    if tool_name in PROFILE_TOOLS:
        return profile_platform_step_key(platform)
    return profile_platform_step_key(platform)


def root_step_keys() -> List[str]:
    """门禁推断用的业务根步（排除 [COLLISION_DEMO_FAKE] 假节点，避免卡在 4.4～4.6）。"""
    return [
        s.step_key
        for s in execution_steps_for_platform("twitter")
        if s.step_key not in COLLISION_DEMO_STEP_KEYS  # [COLLISION_DEMO_FAKE]
    ]


def is_collision_demo_step(step_key: Optional[str]) -> bool:
    """[COLLISION_DEMO_FAKE] 是否为关联碰撞演示假节点。正式版可整函数删除。"""
    return bool(step_key) and str(step_key) in COLLISION_DEMO_STEP_KEYS


def initial_steps(platform: Optional[str] = None) -> List[StepDef]:
    return list(root_steps_for_platform(platform))


def tool_step_key(tool_name: str) -> str:
    return TOOL_PRIMARY_STEP.get(tool_name, "step4_profiles")


PHASE_SHELL_KEYS = frozenset(s.step_key for s in phase_shell_steps())

# 业务直接子步 → L1 七大壳（深叶 3.1.x / 5.1.x 不映射到壳，由 3.1/5.1 收口后再滚壳）
EXECUTION_PARENT_SHELL: Dict[str, str] = {
    s.step_key: str(s.parent_step_key)
    for s in execution_steps_for_platform("twitter")
    if s.parent_step_key
}


def is_phase_shell(step_key: Optional[str]) -> bool:
    return bool(step_key) and str(step_key) in PHASE_SHELL_KEYS


def phase_shell_of_execution_step(step_key: Optional[str]) -> Optional[str]:
    """若 step_key 是挂在七大壳下的直接业务步，返回壳 key。

    注意：EXECUTION_PARENT_SHELL 实际存的是「步 → 直接父」；对 stream 子步父为
    step5_streams（中间父），不是七大壳。深叶请用 immediate_parent_step_key 再向上走。
    """
    if not step_key:
        return None
    return EXECUTION_PARENT_SHELL.get(str(step_key))


def immediate_parent_step_key(step_key: Optional[str]) -> Optional[str]:
    """静态/约定上的直接父节点（不查库）。

    - step7_post_* → step7_posts
    - step7_video_* → step7_post_{platform}
    - step4_profile_* → step4_profiles
    - 其余业务步 → EXECUTION_PARENT_SHELL（可能是中间父或七大壳）
    """
    if not step_key:
        return None
    sk = str(step_key)
    if is_video_platform_step(sk):
        return post_platform_step_key(sk.replace("step7_video_", "", 1))
    if is_post_platform_step(sk):
        return POST_PARENT_STEP_KEY
    if is_profile_platform_step(sk):
        return PROFILE_PARENT_STEP_KEY
    return EXECUTION_PARENT_SHELL.get(sk)


def direct_execution_children(phase_key: str) -> List[str]:
    return [k for k, p in EXECUTION_PARENT_SHELL.items() if p == phase_key]


def step_phase(step_key: str) -> Optional[str]:
    for s in ROOT_STEPS:
        if s.step_key == step_key:
            return s.current_phase
    if is_profile_platform_step(step_key):
        return PHASE_PROFILES
    if is_post_platform_step(step_key) or is_video_platform_step(step_key):
        return PHASE_POSTS
    return None
