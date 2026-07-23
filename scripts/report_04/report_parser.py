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

_SECTION1_HEAD = "一、账号基本信息"

# 终稿正文禁止出现的管线/运维脏数据（命中则不算合法终稿，也不做 8～10 回填）
_DIRTY_META_PATTERNS = (
    re.compile(r"步骤\s*7\.5", re.I),
    re.compile(r"步骤\s*3\.5", re.I),
    re.compile(r"图片资产管线|图片管线", re.I),
    re.compile(r"image_pipeline", re.I),
    re.compile(r"force-?analyze", re.I),
    re.compile(r"ModuleNotFoundError|PYTHONPATH|db_sink", re.I),
    re.compile(r"未部署|未注册|跳过步骤\s*7\.5|跳过步骤\s*3\.5", re.I),
    re.compile(r"\btask_id\b|任务\s*ID\s*[：:]", re.I),
    re.compile(r"collect_images|skip_if_stored|HERMES_REPORT_", re.I),
    re.compile(r"shell\s+hook|Hook\s*超时|120s", re.I),
    re.compile(r"管线未找到|即席执行|非\s*Gateway\s*会话", re.I),
)


def extract_report_body(content: str) -> str:
    """剥掉终稿前的进度/管线元叙述，从「一、账号基本信息」起截取。

    Agent 常在终稿前写「跳过步骤7.5…」导致整段被脏数据门禁拒绝、步骤8～10永久 running。
    """
    raw = (content or "").strip()
    if not raw:
        return ""
    markers = ("## 一、账号基本信息", "# 一、账号基本信息", "一、账号基本信息")
    best = -1
    for m in markers:
        i = raw.find(m)
        if i >= 0 and (best < 0 or i < best):
            best = i
    if best < 0:
        return raw
    return raw[best:].strip()


def is_progress_only(text: str) -> bool:
    """进度播报，不应完成分析步骤。"""
    t = (text or "").strip()
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


def has_report_dirty_meta(content: str) -> bool:
    """终稿是否夹带管线/运维元叙述。"""
    t = content or ""
    return any(p.search(t) for p in _DIRTY_META_PATTERNS)


def starts_with_report_section1(content: str) -> bool:
    """终稿必须以「一、账号基本信息」开头（允许前导空白与可选 ##）。"""
    t = (content or "").lstrip()
    if not t:
        return False
    if t.startswith("##"):
        t = t[2:].lstrip()
    elif t.startswith("#"):
        t = t[1:].lstrip()
    return t.startswith(_SECTION1_HEAD)


def is_final_report(content: str) -> bool:
    """合法步骤11终稿：够长、含≥2个章节、以第一节开头、正文无管线脏数据。

    允许 Agent 在第一节前写进度句；判定时先剥前缀再门禁。
    注意：步骤8/9/10 的独立块走 parse_standalone_analysis_blocks，不经过本函数。
    """
    body = extract_report_body(content)
    if len(body) < 200:
        return False
    if not starts_with_report_section1(body):
        return False
    if has_report_dirty_meta(body):
        return False
    text = body.replace(" ", "").replace("\u3000", "")
    return sum(1 for m in _FINAL_MARKERS if m in text) >= 2


def parse_standalone_analysis_blocks(text: str) -> Dict[str, str]:
    """解析独立步骤 8/9/10 正文块（不依赖 is_final_report，不受终稿门禁影响）。"""
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
    """从步骤十一终稿切片回填 8～10。

    仅对 is_final_report 通过的干净终稿切片；脏数据/非第一节开头的正文不回填，
    避免污染 display。步骤8/9/10 若已由 standalone 块写入则不受影响。
    """
    body = extract_report_body(text)
    if not is_final_report(body):
        return {}
    s8_parts: List[str] = []
    s1 = _extract_section(body, "一、账号基本信息", ("二、", "## 二"))
    if s1:
        s8_parts.append(s1)
    s2 = _extract_section(body, "二、账号全网关联账号", ("三、", "## 三"))
    if "2.3" in s2 or "图片流" in s2:
        s8_parts.append(s2)
    s9 = _extract_section(body, "三、账号网络活动情况", ("四、", "## 四"))
    s10 = _extract_section(body, "四、核查思路", ())
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


def looks_like_entering_analysis_text(text: str) -> bool:
    """是否明确进入步骤8/9/10（非进度句、非终稿）。"""
    t = (text or "").strip()
    if not t or len(t) < 40:
        return False
    if is_progress_only(t) or is_final_report(t):
        return False
    return bool(
        re.search(
            r"步骤\s*[89]\b|步骤\s*10\b|进入步骤\s*[89]|进入步骤\s*10|并行.*步骤\s*[89]",
            t,
            re.I,
        )
    )


def analysis_steps_terminal(status_map: Dict[str, str]) -> bool:
    for k in ANALYSIS_STEP_KEYS:
        if status_map.get(k) not in {"completed", "skipped"}:
            return False
    return True
