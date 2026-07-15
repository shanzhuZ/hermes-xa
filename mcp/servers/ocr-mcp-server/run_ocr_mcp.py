#!/usr/bin/env python3
"""
Hermes OCR MCP 启动器：配置 Windows 下 Tesseract 路径后启动 mcp-ocr。

环境变量:
  TESSERACT_CMD — tesseract.exe 绝对路径（推荐）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _configure_windows_stdio_utf8() -> None:
    """避免 Windows 子进程 GBK 解码错误。"""
    if os.name != "nt":
        return
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if reconf:
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


_configure_windows_stdio_utf8()


def _find_tesseract() -> str:
    env_cmd = (os.environ.get("TESSERACT_CMD") or "").strip().strip('"')
    if env_cmd and Path(env_cmd).is_file():
        return env_cmd
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for p in candidates:
        if Path(p).is_file():
            return p
    return env_cmd


def _configure_tesseract() -> None:
    cmd = _find_tesseract()
    if not cmd or not Path(cmd).is_file():
        print(
            "未找到 Tesseract。请安装: https://github.com/UB-Mannheim/tesseract/wiki\n"
            "并在 config.yaml 的 ocr.env 设置 TESSERACT_CMD，或加入系统 PATH。",
            file=sys.stderr,
        )
        sys.exit(1)

    tess_dir = str(Path(cmd).parent)
    path = os.environ.get("PATH", "")
    if tess_dir.lower() not in path.lower():
        os.environ["PATH"] = tess_dir + os.pathsep + path
    os.environ.setdefault("TESSERACT_CMD", cmd)

    local_tessdata = Path(__file__).resolve().parent / "tessdata"
    if local_tessdata.is_dir() and not os.environ.get("TESSDATA_PREFIX"):
        os.environ["TESSDATA_PREFIX"] = str(local_tessdata)

    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = cmd


def _normalize_ocr_language(language: str) -> str:
    """mcp-ocr 只允许单个已安装语言码；Agent 常传 eng+chi_sim / chi_sim+eng。

    组合串不在 Available languages 列表中会直接 400，导致步骤五反复重试。
    规则：含 chi_sim → 用 chi_sim；否则取第一个已安装片段；都没有则 eng。
    """
    raw = (language or "eng").strip().lower().replace(",", "+").replace(" ", "")
    if not raw:
        return "eng"
    if "+" not in raw:
        return raw
    parts = [p for p in raw.split("+") if p]
    if "chi_sim" in parts:
        return "chi_sim"
    if "eng" in parts:
        return "eng"
    return parts[0]


def _patch_perform_ocr_language(mcp: object) -> None:
    """包装 perform_ocr / image_to_data / perform_batch_ocr，兼容组合 language。"""
    import functools

    tool_manager = getattr(mcp, "_tool_manager", None)
    tools = getattr(tool_manager, "_tools", None) if tool_manager else None
    if not isinstance(tools, dict):
        return

    for name in ("perform_ocr", "image_to_data", "perform_batch_ocr"):
        tool = tools.get(name)
        if tool is None or not getattr(tool, "fn", None):
            continue
        orig = tool.fn

        @functools.wraps(orig)
        async def _wrapped(*args, _orig=orig, **kwargs):  # noqa: ANN001
            if "language" in kwargs and kwargs["language"] is not None:
                kwargs["language"] = _normalize_ocr_language(str(kwargs["language"]))
            elif len(args) >= 2 and isinstance(args[1], str):
                args = (args[0], _normalize_ocr_language(args[1]), *args[2:])
            return await _orig(*args, **kwargs)

        object.__setattr__(tool, "fn", _wrapped)


def main() -> None:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("PYTHONUTF8", "1")

    _configure_tesseract()

    import mcp_ocr.install_tesseract as _inst

    _inst.install_tesseract = lambda: None  # type: ignore[method-assign]

    from mcp_ocr.server import mcp

    _patch_perform_ocr_language(mcp)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
