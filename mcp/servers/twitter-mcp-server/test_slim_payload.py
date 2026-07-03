#!/usr/bin/env python3
"""对比瘦身前后 persona 返回体字符数（合成数据，无需 Twitter 连接）。"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import run_twitter_mcp as tw  # noqa: E402


def _fake_rows(n: int = 151) -> list[dict[str, Any]]:
    themes = [
        "中國內地與政治社會",
        "公民维权与强拆",
        "新闻自由与言论",
        "香港現狀與法治民生",
        "国际与地区",
    ]
    base = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for i in range(n):
        theme = themes[i % len(themes)]
        text = (
            f"【{theme}】样本推文 {i}："
            f"据网友爆料某地发生维权事件，现场视频显示双方发生冲突。"
            f"相关部门尚未回应。#维权 #社会 链接 https://t.co/abc{i}"
        )
        rows.append(
            {
                "id": str(1000000 + i),
                "created_at": (base - timedelta(hours=i * 5)).isoformat(),
                "post_type": "original" if i % 5 else "retweet",
                "likes": 50 + i * 3,
                "retweets": 10 + i,
                "replies": i % 7,
                "text": text,
                "hashtags": ["维权", "社会"],
                "retweet_source": "china_action" if i % 5 == 0 else None,
                "quoted_text": "",
                "retweeted_text": "",
            }
        )
    return rows


def _build_legacy_payload(all_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """瘦身前的字段结构（用于对比）。"""
    timeline_rows = all_rows
    viewpoint = tw._build_viewpoint_stats(all_rows)
    return {
        "screen_name": "whyyoutouzhele",
        "sample_size": len(all_rows),
        "viewpoint_stats": viewpoint,
        "viewpoint_clusters": tw._build_viewpoint_clusters(all_rows, timeline_per_cluster=15),
        "author_viewpoint_clusters": tw._build_author_viewpoint_clusters(
            all_rows, timeline_per_cluster=10
        ),
        "author_voice_sample": tw._build_author_voice(all_rows, limit=25),
        "tweet_evidence_pack": tw._build_evidence_pack(all_rows, limit=35),
        "recent_tweets_compact": tw._compact_tweet_rows(timeline_rows, limit=50),
        "monthly_trends": tw._monthly_counts(timeline_rows),
        "post_type_breakdown": dict(Counter(r["post_type"] for r in all_rows)),
    }


def _build_slim(all_rows: list[dict[str, Any]]) -> dict[str, Any]:
    user = SimpleNamespace(created_at="2019-01-01", statuses_count=50000)
    coverage = {
        "lookback_days_requested": 90,
        "actual_calendar_days_covered": 80,
        "model_writing_hint": "实际样本 151 条，覆盖 80 天",
        "coverage_incomplete": True,
    }
    window = {
        "oldest_in_sample": "2026-03-31",
        "newest_in_sample": "2026-06-18",
        "actual_calendar_days_covered": 80,
    }
    return tw._assemble_slim_persona_payload(
        screen_name="whyyoutouzhele",
        all_rows=all_rows,
        timeline_rows=all_rows,
        reply_rows=[],
        user=user,
        fetch_warnings=[],
        coverage_summary=coverage,
        window_meta=window,
        tl_fetch={"pages_fetched": 8},
        supplemented=False,
        replies_meta={"skipped": True},
        evidence_limit=40,
    )


def main() -> None:
    rows = _fake_rows(151)
    legacy = json.dumps(_build_legacy_payload(rows), ensure_ascii=False)
    slim = json.dumps(_build_slim(rows), ensure_ascii=False)
    evidence_only = json.dumps(
        {
            "tweet_evidence_pack": tw._build_evidence_pack(rows, limit=40),
            "viewpoint_theme_summary": tw._build_viewpoint_theme_summary(rows),
        },
        ensure_ascii=False,
    )

    print("=== persona 返回体字符数对比（151 条合成推文）===")
    print(f"瘦身前（旧结构）: {len(legacy):,} chars  under_100k={len(legacy) < 100_000}")
    print(f"瘦身后（新结构）: {len(slim):,} chars  under_100k={len(slim) < 100_000}")
    print(f"仅立证包估算:     {len(evidence_only):,} chars")
    slim_obj = json.loads(slim)
    pack = slim_obj.get("tweet_evidence_pack") or []
    print(f"evidence 条数: {len(pack)}")
    if pack:
        first_key = list(json.loads(slim).keys())[0]
        print(f"JSON 首字段: {first_key} (应为 analysis_target_lock)")


if __name__ == "__main__":
    main()
