#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把领导确认的大屏展示树写进 agent_node_seed.json（只影响 tree/display）。

规则：
1. 保留全量节点；点击展开仍可看到全部（getChildren 不按 show 过滤）
2. 仅 CURATED 的 L3/L4 设 show=true；其余 L3/L4 show=false
3. 缺的节点自动补建
4. 调 L2 weight/displayCount，使 /tree/display 只渲染本清单

用法：
  python scripts/agent_node/apply_display_curation.py
  python scripts/agent_node/seed_es.py --recreate
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

SEED = Path(__file__).resolve().parent / "agent_node_seed.json"

# tree/display 实际按 weight 升序展示（越小越靠前）
L2_DISPLAY = {
    "cat_report": {"name": "报告生产智能体", "weight": 10, "displayCount": 4},
    "cat_verify": {"name": "账号核查智能体", "weight": 20, "displayCount": 2},
    "cat_collect": {"name": "采集智能体", "weight": 30, "displayCount": 2},
    "cat_scan": {"name": "多平台账号扫描", "weight": 40, "displayCount": 2},
}

# 以用户第二次完整清单为准（报告生产带 L4）
CURATED: Dict[str, List[Tuple[str, str, int, List[Tuple[str, str]]]]] = {
    "cat_verify": [
        (
            "agent_google_map_verify",
            "谷歌地图位置核验Agent",
            100,
            [
                ("geo_recognize", "地理位置识别"),
                ("street_view", "街景扫描"),
                ("place_extract", "地点抽取"),
            ],
        ),
        (
            "agent_rumor_sx",
            "陕西谣言特色库Agent",
            90,
            [
                ("feature_match", "谣言特征匹配"),
                ("topic_agg", "区域话题聚合"),
                ("risk_mark", "高风险样本标记"),
            ],
        ),
    ],
    "cat_scan": [
        (
            "agent_maigret_scan",
            "Maigret跨平台扫描",
            100,
            [
                ("collect", "用户名跨平台收集"),
                ("sites", "站点命中汇总"),
            ],
        ),
        (
            "agent_web_scan",
            "网页检索扫描",
            90,
            [
                ("web_search", "搜索引擎检索"),
                ("web_extract", "页面正文抽取"),
            ],
        ),
    ],
    "cat_collect": [
        (
            "agent_twitter",
            "Twitter-MCP采集",
            100,
            [
                ("posts", "发文采集"),
                ("followers", "关注与粉丝列表采集"),
                ("replies", "推文回复采集"),
            ],
        ),
        (
            "agent_youtube",
            "Youtube-MCP采集",
            90,
            [
                ("channel_list", "频道列表采集"),
                ("subtitle", "视频字幕抽取"),
                ("profile", "主页信息采集"),
            ],
        ),
    ],
    "cat_report": [
        (
            "agent_img_flow",
            "图片流Agent分析",
            100,
            [
                ("multimodal", "多模态分析"),
                ("img_text_align", "图文一致性核验"),
                ("keyframe", "视频关键帧核验"),
            ],
        ),
        (
            "agent_views_cn",
            "观点与涉华分析Agent",
            90,
            [
                ("views_summary", "观点归纳"),
                ("china_stance", "涉华倾向识别"),
            ],
        ),
        (
            "agent_geo_city",
            "籍贯常住地活跃城市分析",
            80,
            [
                ("origin_text", "基于文本流的籍贯抽取"),
                ("reside_mm", "基于多模态的常住地抽取"),
            ],
        ),
        (
            "agent_network_viz",
            "关系网可视化Agent",
            70,
            [
                ("evidence", "关系证据梳理"),
                ("compose", "关系构图"),
            ],
        ),
    ],
}


def _cfg(d: Dict[str, Any]) -> Dict[str, Any]:
    cfg = d.get("displayConfig")
    if not isinstance(cfg, dict):
        cfg = {}
        d["displayConfig"] = cfg
    return cfg


def _ensure_agent(
    by_id: Dict[str, Dict[str, Any]],
    docs: List[Dict[str, Any]],
    agent_id: str,
    parent_id: str,
    name: str,
    weight: int,
    display_count: int,
) -> None:
    d = by_id.get(agent_id)
    if d is None:
        d = {
            "id": agent_id,
            "parentId": parent_id,
            "name": name,
            "level": 3,
            "type": "agent",
            "description": name,
            "status": "online",
            "version": "v1.0",
            "creator": "ops",
            "createTime": "2026-07-02 10:00:00",
            "badgeCount": display_count,
            "allCount": display_count,
            "displayConfig": {
                "show": True,
                "weight": weight,
                "displayCount": display_count,
            },
            "stats": {
                "childCount": display_count,
                "accountCount": 120,
                "platformCount": 1,
                "lastActive": "2 分钟前",
            },
        }
        docs.append(d)
        by_id[agent_id] = d
        return

    d["parentId"] = parent_id
    d["name"] = name
    d["description"] = name
    d["level"] = 3
    d["type"] = "agent"
    cfg = _cfg(d)
    cfg["show"] = True
    cfg["weight"] = weight
    cfg["displayCount"] = display_count


def _ensure_cap(
    by_id: Dict[str, Dict[str, Any]],
    docs: List[Dict[str, Any]],
    agent_id: str,
    suffix: str,
    name: str,
    weight: int,
    seq: int,
) -> str:
    cap_id = f"cap_{agent_id}_{suffix}"
    d = by_id.get(cap_id)
    if d is None:
        d = {
            "id": cap_id,
            "parentId": agent_id,
            "name": name,
            "level": 4,
            "type": "capability",
            "description": f"{name}（大屏展示能力点）",
            "status": "online",
            "version": "v1.0",
            "creator": "ops",
            "createTime": "2026-07-03 12:00:00",
            "badgeCount": max(1, 20 + seq * 7),
            "allCount": 0,
            "displayConfig": {
                "show": True,
                "weight": weight,
                "displayCount": 0,
            },
            "stats": {
                "childCount": 0,
                "accountCount": 80 + seq * 15,
                "platformCount": 1,
                "lastActive": f"{seq + 1} 分钟前",
            },
        }
        docs.append(d)
        by_id[cap_id] = d
        return cap_id

    d["parentId"] = agent_id
    d["name"] = name
    d["description"] = f"{name}（大屏展示能力点）"
    d["level"] = 4
    d["type"] = "capability"
    cfg = _cfg(d)
    cfg["show"] = True
    cfg["weight"] = weight
    cfg["displayCount"] = 0
    return cap_id


def recompute_badges(docs: List[Dict[str, Any]]) -> None:
    """按真实父子关系重算角标（与 show 无关；show 只控制 display 裁剪）。"""
    by_parent: Dict[str, List[Dict[str, Any]]] = {}
    by_id = {d["id"]: d for d in docs}
    for d in docs:
        by_parent.setdefault(d.get("parentId") or "", []).append(d)

    for d in docs:
        if d.get("level") != 4:
            continue
        d.setdefault("stats", {})
        d["stats"]["childCount"] = 0
        d["allCount"] = 0
        acc = int((d.get("stats") or {}).get("accountCount") or 0)
        d["badgeCount"] = max(1, min(99, acc % 97 + 1)) if acc else 1

    for d in docs:
        if d.get("level") != 3:
            continue
        kids = by_parent.get(d["id"], [])
        d.setdefault("stats", {})
        d["stats"]["childCount"] = len(kids)
        d["stats"]["accountCount"] = sum(
            int((k.get("stats") or {}).get("accountCount") or 0) for k in kids
        )
        d["badgeCount"] = len(kids)
        d["allCount"] = len(kids)
        d["stats"]["platformCount"] = max(1, int(d["stats"].get("platformCount") or 1))

    for d in docs:
        if d.get("level") != 2:
            continue
        l3 = by_parent.get(d["id"], [])
        l4_n = 0
        acc = 0
        for a in l3:
            acc += int((a.get("stats") or {}).get("accountCount") or 0)
            l4_n += int((a.get("stats") or {}).get("childCount") or 0)
        d.setdefault("stats", {})
        d["stats"]["childCount"] = len(l3)
        d["stats"]["accountCount"] = acc
        d["stats"]["platformCount"] = max(1, len(l3))
        d["badgeCount"] = len(l3)
        d["allCount"] = len(l3) + l4_n

    root = by_id.get("root")
    if not root:
        return
    l2 = by_parent.get("root", [])
    root.setdefault("stats", {})
    root["stats"]["childCount"] = len(l2)
    root["stats"]["accountCount"] = sum(
        int((c.get("stats") or {}).get("accountCount") or 0) for c in l2
    )
    l3_n = sum(1 for x in docs if x.get("level") == 3)
    l4_n = sum(1 for x in docs if x.get("level") == 4)
    root["badgeCount"] = l3_n
    root["allCount"] = len(l2) + l3_n + l4_n
    root["stats"]["platformCount"] = l3_n
    root["name"] = f"共计包含{l3_n}Agent"


def apply() -> None:
    docs: List[Dict[str, Any]] = json.loads(SEED.read_text(encoding="utf-8"))
    by_id = {d["id"]: d for d in docs}

    for d in docs:
        if d.get("level") in (3, 4):
            _cfg(d)["show"] = False

    for cat_id, meta in L2_DISPLAY.items():
        cat = by_id.get(cat_id)
        if not cat:
            raise RuntimeError(f"缺少 L2 节点: {cat_id}")
        cat["name"] = meta["name"]
        cfg = _cfg(cat)
        cfg["show"] = True
        cfg["weight"] = meta["weight"]
        cfg["displayCount"] = meta["displayCount"]

    root = by_id.get("root")
    if root:
        _cfg(root)["show"] = True
        _cfg(root)["displayCount"] = 4
        _cfg(root)["weight"] = 1000

    curated_agents = set()
    curated_caps = set()
    for cat_id, agents in CURATED.items():
        for agent_id, agent_name, weight, caps in agents:
            curated_agents.add(agent_id)
            _ensure_agent(
                by_id,
                docs,
                agent_id,
                cat_id,
                agent_name,
                weight,
                display_count=len(caps),
            )
            for d in docs:
                if d.get("parentId") == agent_id and d.get("level") == 4:
                    _cfg(d)["show"] = False
            for i, (suffix, cap_name) in enumerate(caps):
                cap_id = _ensure_cap(
                    by_id,
                    docs,
                    agent_id,
                    suffix,
                    cap_name,
                    weight=100 - i * 10,
                    seq=i,
                )
                curated_caps.add(cap_id)

    # 角标按真实全量子节点重算（展示裁剪只靠 show/displayCount）
    recompute_badges(docs)

    SEED.write_text(
        json.dumps(docs, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {SEED}")
    print(f"curated agents={len(curated_agents)} caps={len(curated_caps)}")
    root = by_id["root"]
    print(f"root badge={root['badgeCount']} allCount={root['allCount']} name={root['name']}")
    for cid in L2_DISPLAY:
        c = by_id[cid]
        print(f"  {cid} badge={c['badgeCount']} allCount={c['allCount']} child={c['stats']['childCount']}")
    print("Reload ES: python scripts/agent_node/seed_es.py --recreate")


if __name__ == "__main__":
    apply()
