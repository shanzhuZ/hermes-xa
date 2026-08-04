"""collect_phase_steps.source_tag 取值规则（01/02/03/04 共用）。

库内存 JSON 列表文本：["付费"] / ["开源"] / ["自研"] / ["自研","离线"]；壳为 None。
对照：docs/collect_phase_steps_source_tag.md
"""

from __future__ import annotations

import json
from typing import List, Optional, Sequence

TAG_PAID = "付费"
TAG_OPEN = "开源"
TAG_OFFLINE = "离线"
TAG_SELF = "自研"

MCP_PLATFORMS = frozenset({"twitter", "weibo", "youtube", "bilibili"})
APIFY_PLATFORMS = frozenset({"facebook", "instagram", "tiktok", "telegram", "github"})

# 固定 step_key → 标签列表
FIXED_SOURCE_TAGS = {
    "step2_cross_platform": [TAG_OPEN],
    "step2_maigret": [TAG_OPEN],
    "step3_web_search": [TAG_OPEN],
    "step6_osint_es": [TAG_SELF, TAG_OFFLINE],
    "step1_input_accounts": [TAG_SELF],
    # [COLLISION_DEMO_FAKE] 关联碰撞假节点标签 — 正式版删除下列三行
    "step6_geo_verify": [TAG_OPEN],
    "step6_relation_graph": [TAG_OPEN],
    "step6_rumor_sx": [TAG_OFFLINE, TAG_SELF],
}

# 纯研判（01/02 的 step3_streams 是父壳，不在此集合；03 用 streams_as_self）
SELF_STEP_KEYS = frozenset(
    {
        "step4_text_compare",
        "step4_image_compare",
        "step5_validated",
        "step5_stream_text",
        "step5_stream_image",
        "step6_validated",
        "step8_img_analysis",
        "step9_context_views",
        "step10_context_pii",
        "step11_report",
    }
)

_PLATFORM_PREFIXES = (
    "step4_profile_",
    "step3_profile_",
    "step6_post_",
    "step3_post_",
    "step7_post_",
)


def source_tag_for_platform(platform: Optional[str]) -> Optional[List[str]]:
    """平台通道：MCP→开源，Apify→付费，未知→None。"""
    plat = (platform or "").strip().lower()
    if not plat:
        return None
    if plat in MCP_PLATFORMS:
        return [TAG_OPEN]
    if plat in APIFY_PLATFORMS:
        return [TAG_PAID]
    return None


def source_tag_for_step(
    step_key: str,
    *,
    seed_platform: Optional[str] = None,
    streams_as_self: bool = False,
) -> Optional[List[str]]:
    """按 step_key 解析 source_tag 列表；step1_seed 需传 seed_platform。

    streams_as_self：仅 03 核查的 step3_streams（纯研判）传 True；01/02 父壳勿传。
    """
    key = (step_key or "").strip()
    if not key:
        return None
    if key in FIXED_SOURCE_TAGS:
        return list(FIXED_SOURCE_TAGS[key])
    if key == "step3_streams":
        return [TAG_SELF] if streams_as_self else None
    if key in SELF_STEP_KEYS:
        return [TAG_SELF]
    if key == "step1_seed":
        return source_tag_for_platform(seed_platform)
    if key.startswith("step6_video_") or key.startswith("step7_video_"):
        return [TAG_SELF]
    for prefix in _PLATFORM_PREFIXES:
        if key.startswith(prefix):
            return source_tag_for_platform(key[len(prefix) :])
    return None


def source_tag_json(
    step_key: str,
    *,
    seed_platform: Optional[str] = None,
    streams_as_self: bool = False,
) -> Optional[str]:
    """供 INSERT 的 JSON 文本；无标签返回 None。"""
    tags = source_tag_for_step(
        step_key, seed_platform=seed_platform, streams_as_self=streams_as_self
    )
    if not tags:
        return None
    return json.dumps(tags, ensure_ascii=False)


def dumps_tags(tags: Optional[Sequence[str]]) -> Optional[str]:
    if not tags:
        return None
    return json.dumps(list(tags), ensure_ascii=False)
