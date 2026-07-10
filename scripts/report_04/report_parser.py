"""写报 assistant 文本解析：跳步、分析步骤、终稿。"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from report_04.phases import ANALYSIS_STEP_KEYS

_PROGRESS_PATTERNS = (
    re.compile(r"现在.*步骤\s*[89]|并行执行步骤\s*[89]", re.I),
    re.compile(r"一次性输出.*报告", re.I),
    re.compile(r"准备.*步骤\s*[89]", re.I),
)

_SKIP_STEP2 = re.compile(r"跳过步骤\s*2|不跨平台|单平台", re.I)
_SKIP_STEP34 = re.compile(r"跳过步骤\s*3|跳过步骤\s*3-4|跳过步骤\s*3、4", re.I)

_STEP_BLOCK = re.compile(
    r"步骤\s*(8|9|10)\s*[：:]\s*([\s\S]*?)(?=步骤\s*(?:8|9|10|11)\s*[：:]|$)",
    re.I,
)

_FINAL_MARKERS = (
    "一、账号基本信息",
    "二、账号全网关联账号",
    "三、账号网络活动情况",
    "四、核查思路",
)


def is_progress_only(text: str) -> bool:
    """进度播报，不应完成分析步骤。"""
    t = (text or "").strip()
    if len(t) < 400:
        for pat in _PROGRESS_PATTERNS:
            if pat.search(t):
                return True
    return False


def detect_skip_steps(text: str) -> List[str]:
    """单平台路径：返回应 skipped 的根步骤 key。"""
    if not text:
        return []
    skipped: List[str] = []
    if _SKIP_STEP2.search(text):
        skipped.extend(["step2_maigret", "step3_web_search", "step4_profiles"])
    elif _SKIP_STEP34.search(text):
        skipped.extend(["step3_web_search", "step4_profiles"])
    return skipped


def is_final_report(content: str) -> bool:
    text = (content or "").replace(" ", "").replace("\u3000", "")
    if len(text) < 200:
        return False
    return sum(1 for m in _FINAL_MARKERS if m in text) >= 2


def parse_standalone_analysis_blocks(text: str) -> Dict[str, str]:
    """解析独立步骤 8/9/10 正文块。"""
    out: Dict[str, str] = {}
    mapping = {"8": "step8_img_analysis", "9": "step9_context_views", "10": "step10_context_pii"}
    for m in _STEP_BLOCK.finditer(text or ""):
        key = mapping.get(m.group(1))
        body = (m.group(2) or "").strip()
        if key and len(body) >= 200:
            out[key] = body
    return out


def _extract_section(text: str, start_marker: str, end_markers: Tuple[str, ...]) -> str:
    t = text or ""
    idx = t.find(start_marker)
    if idx < 0:
        return ""
    start = idx + len(start_marker)
    end = len(t)
    for em in end_markers:
        j = t.find(em, start)
        if j >= 0:
            end = min(end, j)
    return t[start:end].strip()


def backfill_analysis_from_report(text: str) -> Dict[str, str]:
    """从步骤十一终稿切片回填 8～10。"""
    if not is_final_report(text):
        return {}
    s8_parts: List[str] = []
    s1 = _extract_section(text, "一、账号基本信息", ("二、", "## 二"))
    if s1:
        s8_parts.append(s1)
    s2 = _extract_section(text, "二、账号全网关联账号", ("三、", "## 三"))
    if "2.3" in s2 or "图片流" in s2:
        s8_parts.append(s2)
    s9 = _extract_section(text, "三、账号网络活动情况", ("四、", "## 四"))
    s10 = _extract_section(text, "四、核查思路", ())
    if not s10 and s9:
        s10 = s9
    out: Dict[str, str] = {}
    if s8_parts:
        out["step8_img_analysis"] = "\n\n".join(s8_parts).strip()
    if s9:
        out["step9_context_views"] = s9
    if s10:
        out["step10_context_pii"] = s10
    return out


def mentions_analysis_steps(text: str) -> bool:
    return bool(re.search(r"步骤\s*[89]|步骤\s*10|图片流分析|发文观点|涉华|圈层", text or "", re.I))


def analysis_steps_terminal(status_map: Dict[str, str]) -> bool:
    for k in ANALYSIS_STEP_KEYS:
        if status_map.get(k) not in {"completed", "skipped"}:
            return False
    return True
