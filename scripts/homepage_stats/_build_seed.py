# -*- coding: utf-8 -*-
"""生成 homepage_stats 种子：1 条 summary + 近 7 天 daily。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

OUT = Path(__file__).resolve().parent / "homepage_stats_seed.json"

# 写死 Agent 数与比率（比率单位：百分比整数，如 98 表示 98%）
AGENT_TOTAL = 1021
AGENT_ONLINE_RATE = 97
AGENT_ONLINE = int(round(AGENT_TOTAL * AGENT_ONLINE_RATE / 100.0))  # 990
HISTORY_TASK_COMPLETION_RATE = 98

# 以「今天」为终点的近 7 天假数据（可按需改基准日）
END = datetime(2026, 8, 3)

# 近 7 天：社交/业务=MCP 处理量；核查/写报=任务条数（每天个位数）
DAILY = [
    # date_offset_from_end, social, business, verify, report
    (6, 1820, 420, 5, 3),
    (5, 1950, 380, 4, 6),
    (4, 1680, 450, 7, 2),
    (3, 2100, 510, 3, 5),
    (2, 1880, 470, 6, 4),
    (1, 2050, 490, 2, 7),
    (0, 960, 220, 3, 2),  # 今天
]


def main() -> None:
    docs = []
    today_usage = 0
    now = "2026-08-03 12:00:00"

    for offset, social, business, verify, report in DAILY:
        day = END - timedelta(days=offset)
        date_str = day.strftime("%Y-%m-%d")
        if offset == 0:
            # 今日用量只等于写报+核查（任务条数），与 social/business（MCP 处理量）无关
            today_usage = verify + report
        docs.append(
            {
                "id": f"daily_{date_str}",
                "type": "daily",
                "date": date_str,
                "verifyCount": verify,
                "reportCount": report,
                "socialCount": social,
                "businessCount": business,
                "updatedAt": now,
                "remark": "seed",
            }
        )

    # 历史任务总数：只统计「任务条数」口径（写报+核查），不含 social/business（MCP 数据处理量）
    # 量级控制在一千多：基数约 900 + 近7天写报核查之和
    task_sum = sum(v + r for _, _, _, v, r in DAILY)
    history_task_total = 900 + task_sum

    docs.insert(
        0,
        {
            "id": "summary",
            "type": "summary",
            "todayUsage": today_usage,
            "historyTaskTotal": history_task_total,
            "historyTaskCompletionRate": HISTORY_TASK_COMPLETION_RATE,
            "agentTotal": AGENT_TOTAL,
            "agentOnline": AGENT_ONLINE,
            "agentOnlineRate": AGENT_ONLINE_RATE,
            "updatedAt": now,
            "remark": "seed",
        },
    )

    OUT.write_text(json.dumps(docs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} docs={len(docs)} todayUsage={today_usage} history={history_task_total}")


if __name__ == "__main__":
    main()
