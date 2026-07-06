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


def main() -> None:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    os.environ.setdefault("PYTHONUTF8", "1")

    _configure_tesseract()

    import mcp_ocr.install_tesseract as _inst

    _inst.install_tesseract = lambda: None  # type: ignore[method-assign]

    from mcp_ocr.server import mcp

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
