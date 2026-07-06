"""
Maigret MCP Server — 用户名 OSINT 调查（封装 maigret CLI 全功能）

上游: https://github.com/soxoj/maigret
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

if os.name == "nt":
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for _stream in (sys.stdout, sys.stderr):
        _reconf = getattr(_stream, "reconfigure", None)
        if _reconf:
            try:
                _reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass

mcp = FastMCP("maigret")


def _persona_gate_enabled() -> bool:
    """画像成稿门禁（persona_draft_gate）。hermes-xa 01 采集须保持关闭。"""
    return os.environ.get("MAIGRET_PERSONA_GATE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


# ── 环境配置 ──────────────────────────────────────────────────────────
MAIGRET_BIN = os.environ.get("MAIGRET_BIN", "maigret")
MAIGRET_LAUNCHER = os.environ.get(
    "MAIGRET_LAUNCHER",
    str(Path(__file__).resolve().parent / "run_maigret_cli.py"),
)
# 必须用 resolve()；Windows 下 config 里反斜杠路径易被 YAML 误解析
REPORTS_DIR = Path(os.environ.get("MAIGRET_REPORTS_DIR", "/tmp/maigret_reports")).expanduser().resolve()
DEFAULT_PROXY = os.environ.get("MAIGRET_PROXY") or os.environ.get("HTTP_PROXY", "")
DEFAULT_TIMEOUT = int(os.environ.get("MAIGRET_DEFAULT_TIMEOUT", "600"))
MAX_TIMEOUT = int(os.environ.get("MAIGRET_MAX_TIMEOUT", "600"))
DEFAULT_MAX_CONNECTIONS = int(os.environ.get("MAIGRET_MAX_CONNECTIONS", "15"))
DEFAULT_RETRIES = int(os.environ.get("MAIGRET_RETRIES", "2"))
DEFAULT_REQUEST_TIMEOUT = int(os.environ.get("MAIGRET_REQUEST_TIMEOUT", "15"))
GRACEFUL_STOP_SECONDS = int(os.environ.get("MAIGRET_GRACEFUL_STOP_SECONDS", "20"))
REPORT_POLL_SECONDS = int(os.environ.get("MAIGRET_REPORT_POLL_SECONDS", "25"))

REPORTS_DIR.mkdir(parents=True, exist_ok=True)

SOCIAL_KEYWORDS = (
    "twitter", "instagram", "facebook", "tiktok", "weibo",
    "linkedin", "youtube", "x.com",
)
TECH_KEYWORDS = (
    "github", "gitlab", "stackoverflow", "leetcode", "dev", "npm", "pypi",
)
FORUM_KEYWORDS = ("reddit", "forum", "board", "community", "discuss")


# ── maigret CLI 封装 ──────────────────────────────────────────────────

def _safe_username(username: str) -> str:
    return username.replace("/", "_").replace("\\", "_")


def _effective_timeout(timeout: int | None) -> int:
    """子进程扫描硬上限（默认 10 分钟）。"""
    return min(max(timeout or DEFAULT_TIMEOUT, 30), MAX_TIMEOUT)


def _graceful_stop_process(proc: subprocess.Popen[str]) -> None:
    """温和中断 maigret，尽量触发 partial report 写入。"""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            # CREATE_NEW_PROCESS_GROUP 下 Ctrl+Break 可送达子进程
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
    except (ProcessLookupError, OSError, ValueError, AttributeError):
        try:
            proc.terminate()
        except OSError:
            pass

    try:
        proc.wait(timeout=GRACEFUL_STOP_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        proc.terminate()
        proc.wait(timeout=10)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass


def _build_maigret_subprocess_env() -> dict[str, str]:
    """
    子进程环境：去掉 HTTP_PROXY 等，避免 aiohttp trust_env 与 --proxy 冲突。
    代理仅通过 maigret --proxy 传递（MAIGRET_PROXY）。
    """
    env = os.environ.copy()
    for key in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ):
        env.pop(key, None)
    # Windows 控制台 GBK 会导致 maigret/colorama UnicodeEncodeError，拖慢或搞乱 stderr
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    return env


def _maigret_command(argv_tail: list[str]) -> list[str]:
    """构建 maigret 启动命令（默认走带 curl_cffi 补丁的 launcher）。"""
    launcher = Path(MAIGRET_LAUNCHER)
    if launcher.is_file():
        return [sys.executable, "-u", str(launcher), *argv_tail]
    return [MAIGRET_BIN, *argv_tail]


_WORKER_SCRIPT = Path(__file__).resolve().parent / "maigret_collect_worker.py"


async def _collect_accounts_via_worker(params: dict[str, Any]) -> dict[str, Any]:
    """
    经独立 worker 子进程执行 collect，修复 Windows FastMCP stdio 下 maigret 子进程卡死。
    参数经临时 JSON 文件传递（避免 stdin 管道在嵌套 stdio 下丢数据导致 1～2s 即失败）。
    """
    scan_timeout = _effective_timeout(params.get("timeout"))
    # 须覆盖：scan 上限 + 温和中断 + 写报告；Hermes stdio 下略增缓冲
    wait_limit = scan_timeout + GRACEFUL_STOP_SECONDS + 60
    env = _build_maigret_subprocess_env()
    for key, val in os.environ.items():
        if key.startswith("MAIGRET_"):
            env[key] = val
    env.setdefault("MAIGRET_REPORTS_DIR", str(REPORTS_DIR))
    if DEFAULT_PROXY:
        env.setdefault("MAIGRET_PROXY", DEFAULT_PROXY)

    params_path = ""
    try:
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".json",
            delete=False,
            dir=str(REPORTS_DIR),
        ) as tf:
            json.dump(params, tf, ensure_ascii=False)
            params_path = tf.name

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(_WORKER_SCRIPT),
            params_path,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(_WORKER_SCRIPT.parent),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=wait_limit,
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            partial = _try_recover_partial_result(params, scan_timeout=scan_timeout)
            if partial:
                return partial
            empty = _enrich_collect_payload({
                "timed_out": True,
                "scan_timeout_seconds": scan_timeout,
                "partial_result": False,
                "summary": {"found_count": 0, "accounts": []},
            })
            return empty
    finally:
        if params_path:
            try:
                os.unlink(params_path)
            except OSError:
                pass

    err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
    text = (stdout or b"").decode("utf-8", errors="replace").strip()

    if text:
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                if payload.get("error") and "user_message" not in payload:
                    payload["user_message"] = "Maigret 扫描失败，继续种子账号流校验与单平台采集。"
                    payload["scan_status"] = payload.get("scan_status") or "failed"
                return payload
        except json.JSONDecodeError:
            pass

    return {
        "error": err_text or f"worker exit {proc.returncode}",
        "timed_out": False,
        "scan_status": "failed",
        "user_message": "Maigret 扫描失败，继续种子账号流校验与单平台采集。",
        "summary": {"found_count": 0, "accounts": []},
    }


def _popen_maigret(
    cmd: list[str],
    output_dir: str,
    *,
    stdout_target: Any,
    stderr_target: Any,
) -> subprocess.Popen[str]:
    """启动 maigret 子进程（Windows 需独立进程组 + 关闭 stdin，避免 Hermes stdio 继承管道卡死）。"""
    popen_kw: dict[str, Any] = {
        "stdout": stdout_target,
        "stderr": stderr_target,
        "stdin": subprocess.DEVNULL,
        "cwd": output_dir,
        "encoding": "utf-8",
        "errors": "replace",
        "env": _build_maigret_subprocess_env(),
    }
    if sys.platform == "win32":
        popen_kw["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
    return subprocess.Popen(cmd, **popen_kw)


def _communicate_with_watchdog(
    proc: subprocess.Popen[str],
    scan_timeout: int,
) -> tuple[str, str, bool]:
    """
    用看门狗线程保证到点必停（修复 Windows 上 communicate(timeout) 不可靠的问题）。
    返回 (stdout, stderr, timed_out)。
    """
    finished = threading.Event()
    timed_out_flag = {"value": False}

    def _watchdog() -> None:
        if finished.wait(scan_timeout):
            return
        if proc.poll() is None:
            timed_out_flag["value"] = True
            _graceful_stop_process(proc)

    watcher = threading.Thread(target=_watchdog, daemon=True)
    watcher.start()
    try:
        stdout, stderr = proc.communicate()
        return stdout or "", stderr or "", timed_out_flag["value"]
    finally:
        finished.set()
        watcher.join(timeout=2)


def _run_maigret_subprocess(
    cmd: list[str],
    output_dir: str,
    scan_timeout: int,
) -> tuple[str, str, bool, int | None]:
    """
    运行 maigret 子进程。

    Hermes 以 stdio 托管 MCP 时，子进程再用 PIPE 易缓冲死锁（扫满 timeout 无报告）。
    统一把 stdout/stderr 写入 output_dir 日志文件，再读回尾部。
    """
    out_log = Path(output_dir) / "_maigret_stdout.log"
    err_log = Path(output_dir) / "_maigret_stderr.log"
    fout = open(out_log, "w", encoding="utf-8", errors="replace", buffering=1)
    ferr = open(err_log, "w", encoding="utf-8", errors="replace", buffering=1)
    proc: subprocess.Popen[str] | None = None
    timed_out = False
    try:
        proc = _popen_maigret(cmd, output_dir, stdout_target=fout, stderr_target=ferr)
        finished = threading.Event()
        timed_out_flag = {"value": False}

        def _watchdog() -> None:
            if finished.wait(scan_timeout):
                return
            if proc and proc.poll() is None:
                timed_out_flag["value"] = True
                _graceful_stop_process(proc)

        watcher = threading.Thread(target=_watchdog, daemon=True)
        watcher.start()
        try:
            returncode = proc.wait()
            timed_out = timed_out_flag["value"]
        finally:
            finished.set()
            watcher.join(timeout=2)
    finally:
        fout.close()
        ferr.close()

    stdout = out_log.read_text(encoding="utf-8", errors="replace") if out_log.exists() else ""
    stderr = err_log.read_text(encoding="utf-8", errors="replace") if err_log.exists() else ""
    # 控制返回体体积
    return stdout[-8000:], stderr[-8000:], timed_out, returncode if proc else None


def _run_maigret(
    usernames: list[str],
    *,
    output_dir: str,
    timeout: int | None = None,
    top_sites: int | None = 100,
    all_sites: bool = False,
    tags: str = "",
    exclude_tags: str = "",
    keywords: list[str] | None = None,
    sites: list[str] | None = None,
    proxy: str = "",
    tor_proxy: str = "",
    i2p_proxy: str = "",
    cloudflare_bypass: bool = False,
    with_domains: bool = False,
    request_timeout: int | None = None,
    retries: int | None = None,
    max_connections: int | None = None,
    no_recursion: bool = False,
    json_type: str = "simple",
    html: bool = False,
    pdf: bool = False,
    csv: bool = False,
    txt: bool = False,
    graph: bool = False,
    xmind: bool = False,
    markdown: bool = False,
    parse_url: str = "",
    permute: bool = False,
    ai: bool = False,
    ai_model: str = "",
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    """执行 maigret CLI，返回 stdout/stderr/退出码及输出目录。"""
    if not usernames and not parse_url:
        raise ValueError("至少提供一个 username 或 parse_url")

    effective_timeout = _effective_timeout(timeout)
    os.makedirs(output_dir, exist_ok=True)

    cmd: list[str] = _maigret_command([
        "--no-color",
        "--no-progressbar",
        "--folderoutput",
        output_dir,
        "--json",
        json_type,
        "-n",
        str(max_connections or DEFAULT_MAX_CONNECTIONS),
        "--retries",
        str(retries if retries is not None else DEFAULT_RETRIES),
        "--timeout",
        str(request_timeout or DEFAULT_REQUEST_TIMEOUT),
    ])

    if no_recursion:
        cmd.append("--no-recursion")

    if parse_url:
        cmd.extend(["--parse", parse_url])
    else:
        cmd.extend(usernames)

    if all_sites:
        cmd.append("-a")
    elif top_sites is not None:
        cmd.extend(["--top-sites", str(top_sites)])

    if tags:
        cmd.extend(["--tags", tags])
    if exclude_tags:
        cmd.extend(["--exclude-tags", exclude_tags])
    if keywords:
        cmd.extend(["--keywords", *keywords])
    if sites:
        for site in sites:
            cmd.extend(["--site", site])

    effective_proxy = proxy or DEFAULT_PROXY
    if effective_proxy:
        cmd.extend(["--proxy", effective_proxy])
    if tor_proxy:
        cmd.extend(["--tor-proxy", tor_proxy])
    if i2p_proxy:
        cmd.extend(["--i2p-proxy", i2p_proxy])
    if cloudflare_bypass:
        cmd.append("--cloudflare-bypass")
    if with_domains:
        cmd.append("--with-domains")

    if permute and len(usernames) > 1:
        cmd.append("--permute")

    if html:
        cmd.append("--html")
    if pdf:
        cmd.append("--pdf")
    if csv:
        cmd.append("--csv")
    if txt:
        cmd.append("--txt")
    if graph:
        cmd.append("--graph")
    if xmind:
        cmd.append("--xmind")
    if markdown:
        cmd.append("--md")
    if ai:
        cmd.append("--ai")
        if ai_model:
            cmd.extend(["--ai-model", ai_model])

    if extra_args:
        cmd.extend(extra_args)

    timed_out = False
    stdout = ""
    stderr = ""

    try:
        stdout, stderr, timed_out, returncode = _run_maigret_subprocess(
            cmd, output_dir, effective_timeout
        )
        if timed_out:
            stderr = (
                (stderr or "")
                + f"\n[maigret_mcp] 扫描已达 {effective_timeout}s 上限，已温和中断并尝试保留部分结果"
            ).strip()
        return {
            "command": " ".join(cmd),
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode if returncode is not None else -1,
            "output_dir": output_dir,
            "success": returncode == 0 and not timed_out,
            "timed_out": timed_out,
            "scan_timeout_seconds": effective_timeout,
        }
    except FileNotFoundError:
        return {
            "command": " ".join(cmd),
            "stdout": "",
            "stderr": (
                f"未找到 {MAIGRET_BIN}，请安装: pip install maigret "
                "或设置 MAIGRET_BIN 环境变量"
            ),
            "returncode": -1,
            "output_dir": output_dir,
            "success": False,
            "timed_out": False,
            "scan_timeout_seconds": effective_timeout,
        }


def _json_report_path(output_dir: str, username: str, json_type: str = "simple") -> Path:
    safe = _safe_username(username)
    return Path(output_dir) / f"report_{safe}_{json_type}.json"


def _list_report_files(output_dir: str) -> list[str]:
    p = Path(output_dir)
    if not p.exists():
        return []
    return sorted(str(f.resolve()) for f in p.iterdir() if f.is_file())


def _find_html_report(output_dir: str, username: str) -> str | None:
    """maigret 生成的 HTML 名为 report_{username}_plain.html。"""
    safe = _safe_username(username)
    p = Path(output_dir)
    candidates = [
        p / f"report_{safe}_plain.html",
        p / f"report_{safe}.html",
    ]
    for c in candidates:
        if c.exists():
            return str(c.resolve())
    # 回退：目录内任意 html
    html_files = sorted(p.glob("*.html"))
    return str(html_files[0].resolve()) if html_files else None


def _extract_ids_from_url(url: str, request_timeout: int = 30) -> dict[str, Any]:
    """
    仅解析 URL 页面提取 ID/用户名（秒级），不跑全站扫描。
    对应 maigret --parse 的第一步。
    """
    import logging
    from maigret.maigret import extract_ids_from_page

    logger = logging.getLogger("maigret_mcp")
    try:
        extracted = extract_ids_from_page(url, logger, timeout=request_timeout)
        return {
            "success": True,
            "extracted_ids": extracted,
            "usernames": [
                name for name, id_type in extracted.items() if id_type == "username"
            ],
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "extracted_ids": {},
            "usernames": [],
        }


def _load_json_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)


def _is_claimed(info: dict[str, Any]) -> bool:
    status = info.get("status")
    if isinstance(status, str):
        return status == "Claimed"
    if isinstance(status, dict):
        return status.get("status") == "Claimed"
    return False


def _get_ids_data(info: dict[str, Any]) -> dict[str, Any]:
    if info.get("ids_data"):
        return info["ids_data"]
    status = info.get("status")
    if isinstance(status, dict) and status.get("ids_data"):
        return status["ids_data"]
    return {}


def _get_discovered_usernames(info: dict[str, Any]) -> list[str]:
    found: set[str] = set()
    for key in ("ids_usernames",):
        raw = info.get(key)
        if isinstance(raw, dict):
            found.update(str(u) for u in raw.keys())
    status = info.get("status")
    if isinstance(status, dict):
        raw = status.get("ids_usernames")
        if isinstance(raw, dict):
            found.update(str(u) for u in raw.keys())
    ids = _get_ids_data(info)
    for field in ("username", "uid", "instagram", "telegram"):
        val = ids.get(field)
        if val and isinstance(val, str) and re.match(r"^[\w.\-]{2,}$", val):
            found.add(val)
    return list(found)


def _extract_claimed_accounts(data: dict[str, Any]) -> dict[str, Any]:
    return {
        site: info for site, info in data.items()
        if isinstance(info, dict) and _is_claimed(info)
    }


def _extract_pii(accounts: dict[str, Any]) -> dict[str, Any]:
    pii: dict[str, Any] = {
        "names": set(),
        "emails": set(),
        "phones": set(),
        "locations": set(),
        "bio": set(),
        "avatars": [],
        "urls": [],
        "extra_usernames": set(),
    }
    for site, info in accounts.items():
        ids = _get_ids_data(info)
        if ids.get("fullname"):
            pii["names"].add(str(ids["fullname"]))
        if ids.get("name"):
            pii["names"].add(str(ids["name"]))
        if ids.get("email"):
            pii["emails"].add(str(ids["email"]))
        if ids.get("phone"):
            pii["phones"].add(str(ids["phone"]))
        if ids.get("location"):
            pii["locations"].add(str(ids["location"]))
        if ids.get("bio"):
            pii["bio"].add(str(ids["bio"]))
        if ids.get("image"):
            pii["avatars"].append({"site": site, "url": ids["image"]})
        url = info.get("url_user") or ids.get("url")
        if url:
            pii["urls"].append({"site": site, "url": url})
        for u in _get_discovered_usernames(info):
            pii["extra_usernames"].add(u)
    return {
        k: sorted(v) if isinstance(v, set) else v
        for k, v in pii.items()
    }


# 常见平台 URL → handle 正则
_PROFILE_URL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("twitter", re.compile(r"(?:twitter|x)\.com/([A-Za-z0-9_]{1,15})(?:/|$|\?)", re.I)),
    ("telegram", re.compile(r"t\.me/([A-Za-z0-9_]{4,})(?:/|$|\?)", re.I)),
    ("youtube", re.compile(r"youtube\.com/@([A-Za-z0-9_.-]+)", re.I)),
    ("github", re.compile(r"github\.com/([A-Za-z0-9_-]+)", re.I)),
    ("instagram", re.compile(r"instagram\.com/([A-Za-z0-9_.]+)", re.I)),
    ("tiktok", re.compile(r"tiktok\.com/@([A-Za-z0-9_.]+)", re.I)),
    # facebook：取 vanity handle，排除 profile.php / people / pages / groups 等保留路径
    ("facebook", re.compile(
        r"facebook\.com/(?!profile\.php|people/|pages/|groups/|watch|marketplace|events|story\.php)"
        r"([A-Za-z0-9.]{3,})(?:/|$|\?)", re.I)),
]


def _resolve_username(username: str = "", profile_url: str = "") -> tuple[str, dict[str, Any]]:
    """从 username 或平台主页 URL 解析 maigret 搜索用的 handle。"""
    if username and username.strip():
        return username.strip().lstrip("@"), {"source": "username"}

    url = (profile_url or "").strip()
    if not url:
        raise ValueError("username 或 profile_url 至少提供一个")

    for platform, pattern in _PROFILE_URL_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1), {"source": "url_pattern", "platform": platform, "profile_url": url}

    extracted = _extract_ids_from_url(url, request_timeout=45)
    names = extracted.get("usernames") or []
    if names:
        return names[0], {"source": "page_extract", "profile_url": url, "extraction": extracted}

    raise ValueError(f"无法从 URL 解析用户名: {url}")


def _load_ndjson_report(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _summarize_maigret_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """从 ndjson/simple 记录生成精简摘要（完整数据在报告文件）。"""
    accounts: list[dict[str, Any]] = []
    discovered_usernames: set[str] = set()
    discovered_links: set[str] = set()

    for rec in records:
        status = rec.get("status")
        if isinstance(status, str):
            if status != "Claimed":
                continue
            sitename = rec.get("sitename") or str(rec.get("site", "unknown"))
            accounts.append({
                "sitename": sitename,
                "url": rec.get("url_user", ""),
                "ids": _get_ids_data(rec),
                "tags": [],
            })
            continue

        if not isinstance(status, dict) or status.get("status") != "Claimed":
            continue

        sitename = rec.get("sitename") or status.get("site_name") or "unknown"
        ids = status.get("ids") or _get_ids_data(rec)
        if not isinstance(ids, dict):
            ids = {"raw": ids} if ids else {}
        url_user = rec.get("url_user") or status.get("url", "") or ""
        if "youtube" in str(sitename).lower() and url_user:
            m = re.search(r"youtube\.com/channel/(UC[\w-]+)", url_user, re.I)
            if m:
                ids = dict(ids)
                ids["youtube_channel_id"] = m.group(1)
        accounts.append({
            "sitename": sitename,
            "url": url_user,
            "ids": ids,
            "tags": status.get("tags") or [],
        })

        for u in (rec.get("ids_usernames") or {}).keys():
            discovered_usernames.add(str(u))
        for link in rec.get("ids_links") or []:
            discovered_links.add(str(link))
        # bio 里的链接
        links_raw = ids.get("links") if isinstance(ids, dict) else None
        if isinstance(links_raw, str) and links_raw.startswith("["):
            try:
                for link in json.loads(links_raw.replace("'", '"')):
                    discovered_links.add(str(link))
            except json.JSONDecodeError:
                pass

    return {
        "found_count": len(accounts),
        "accounts": accounts,
        "discovered_usernames": sorted(discovered_usernames),
        "discovered_links": sorted(discovered_links),
    }


def _poll_report_records(
    report_path: Path,
    json_format: str,
    *,
    max_wait: int | None = None,
) -> list[dict[str, Any]]:
    """温和中断后等待 maigret 落盘（ndjson 常在进程退出时一次性写出）。"""
    import time

    deadline = time.time() + (max_wait if max_wait is not None else REPORT_POLL_SECONDS)
    while time.time() < deadline:
        if report_path.exists() and report_path.stat().st_size > 2:
            records = _load_claimed_records(report_path, json_format)
            if records:
                return records
        time.sleep(0.5)
    return []


def _maigret_user_message(
    *,
    found_count: int,
    timed_out: bool,
    partial: bool,
    scan_timeout: int,
) -> str:
    """01 采集步骤 2：中性状态说明，禁止引导写报告。"""
    if timed_out and found_count == 0:
        return f"跨平台扫描超时（{scan_timeout}s），无候选；继续步骤 3 仅采种子平台。"
    if found_count > 0:
        if timed_out or partial:
            return (
                f"跨平台扫描达 {scan_timeout}s 上限，已返回 {found_count} 个候选"
                f"（见 summary.accounts，继续步骤 3 采各候选主页）。"
            )
        return (
            f"跨平台扫描完成，{found_count} 个候选（见 summary.accounts）。"
            "继续步骤 3：有 MCP 采 profile，无 MCP 用 Apify；禁止写报告。"
        )
    return "跨平台扫描完成，无其它平台候选；继续步骤 3 仅处理种子相关外链。"


def _maigret_apify_tool(actor_slug: str) -> str:
    """headlessagent/facebook-... → mcp_apify_headlessagent__facebook_profile_post_scraper"""
    return "mcp_apify_" + actor_slug.replace("/", "__").replace("-", "_")


def _build_step3_actions(accounts: list[dict[str, Any]], seed_username: str) -> list[dict[str, Any]]:
    """为 01 采集步骤 3 生成可执行工具清单（机器可读，避免 Agent 猜工具名）。"""
    actions: list[dict[str, Any]] = []
    seed = (seed_username or "").strip().lstrip("@").lower()
    discovery_only = (
        "imginn", "picuki", "wordpress", "blogger", "discord", "githubgist", "pinterest"
    )

    for acc in accounts:
        sitename = str(acc.get("sitename") or "")
        low_site = sitename.lower()
        url = (acc.get("url") or "").strip()
        ids = acc.get("ids") if isinstance(acc.get("ids"), dict) else {}

        if any(d in low_site for d in discovery_only):
            actions.append({
                "platform": sitename,
                "url": url,
                "action": "register_only",
                "reason": "discovery_only，步骤3不调工具",
            })
            continue

        if "youtube" in low_site:
            ch = ids.get("youtube_channel_id") or ids.get("channel_id")
            if ch:
                actions.append({
                    "platform": "youtube",
                    "url": url,
                    "tool": "mcp_youtube_get_channel_stats",
                    "args": {"channelId": str(ch)},
                })
            else:
                actions.append({
                    "platform": "youtube",
                    "url": url,
                    "action": "skip",
                    "reason": "无 ids.youtube_channel_id，跳过",
                })
            continue

        if "instagram" in low_site and url:
            actions.append({
                "platform": "instagram",
                "url": url,
                "step3_apify": [
                    {
                        "tool": _maigret_apify_tool("apify/instagram-scraper"),
                        "args": {"directUrls": [url.rstrip("/") + "/"], "resultsLimit": 5},
                    },
                    {"tool": "mcp_apify_get_actor_run", "args_from_previous_run": True},
                    {"tool": "mcp_apify_get_dataset_items", "args": {"limit": 20}},
                ],
            })
            continue

        if "tiktok" in low_site:
            prof = url
            if not prof and seed:
                prof = f"https://www.tiktok.com/@{seed}"
            if prof:
                actions.append({
                    "platform": "tiktok",
                    "url": prof,
                    "step3_apify": [
                        {
                            "tool": _maigret_apify_tool("clockworks/tiktok-scraper"),
                            "args": {"profiles": [prof], "resultsPerPage": 5},
                        },
                        {"tool": "mcp_apify_get_actor_run", "args_from_previous_run": True},
                        {"tool": "mcp_apify_get_dataset_items", "args": {"limit": 20}},
                    ],
                })
            continue

        if "telegram" in low_site:
            ch = seed
            m = re.search(r"t\.me/([A-Za-z0-9_]+)", url, re.I)
            if m:
                ch = m.group(1)
            if ch:
                actions.append({
                    "platform": "telegram",
                    "url": url,
                    "step3_apify": [
                        {
                            "tool": _maigret_apify_tool("vujeen/telegram-channel-scraper"),
                            "args": {"channels": [ch], "maxPostsPerChannel": 3},
                        },
                        {"tool": "mcp_apify_get_actor_run", "args_from_previous_run": True},
                        {"tool": "mcp_apify_get_dataset_items", "args": {"limit": 20}},
                    ],
                })
            continue

        if "twitter" in low_site or "x.com" in url.lower():
            handle = seed
            m = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})", url, re.I)
            if m:
                handle = m.group(1)
            if handle.lower() == seed:
                actions.append({
                    "platform": "twitter",
                    "url": url,
                    "action": "skip",
                    "reason": "与种子相同，步骤1已采",
                })
            else:
                actions.append({
                    "platform": "twitter",
                    "url": url,
                    "tool": "mcp_twitter_get_user_info",
                    "args": {"screen_name": handle},
                })
            continue

        actions.append({
            "platform": sitename,
            "url": url,
            "action": "skip",
            "reason": "无步骤3 MCP/Apify 映射",
        })

    return actions


def _attach_collect_skill_hint(payload: dict[str, Any]) -> dict[str, Any]:
    """hermes-xa 01 采集：剥离画像成稿字段，注入 Skill 后续步骤提示。"""
    out = dict(payload)
    for key in (
        "persona_draft_gate",
        "DRAFT_BLOCKED",
        "DRAFT_BLOCKED_REASON",
        "NEXT_ACTIONS_REQUIRED",
        "cross_platform_collection_plan",
        "mandatory_output_contract",
    ):
        out.pop(key, None)
    accounts = (out.get("summary") or {}).get("accounts") or []
    seed_username = str(out.get("username") or "")
    step3 = _build_step3_actions(accounts, seed_username)
    out["collect_skill_mode"] = True
    out["output_language"] = "zh-CN"
    out["seed_username"] = seed_username
    out["step3_actions"] = step3
    out["agent_must_not"] = [
        "写人物画像/综合报告/Investigation Report/用户画像总结",
        "使用英文长报告（必须简体中文）",
        "调用 web_search 或 web_extract 或 browser_*",
        "在步骤 3 调用 get_user_tweets / analyze_channel_videos / get_user_feeds",
        "使用 mcp_apify_*_get_dataset_items 等拼接工具名（dataset 用 mcp_apify_get_dataset_items）",
        "把本工具返回当作最终输出",
    ]
    out["agent_must_do_next"] = [
        "严格按 step3_actions 逐步调工具；失败则 skip，禁止 web_search",
        "步骤4: 文本流+图片流(OCR+vision)；步骤5: validated_accounts；步骤6: 发文；步骤7: 仅三节中文报告",
    ]
    out["hint"] = (
        f"种子 @{seed_username}；{len(accounts)} 条候选。"
        f"步骤3 见 step3_actions（{len(step3)} 条）。禁止写报告。"
    )
    return out


def _enrich_collect_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """补充 scan_status / user_message。"""
    summary = payload.get("summary") or {}
    found = int(summary.get("found_count") or 0)
    timed_out = bool(payload.get("timed_out"))
    partial = bool(payload.get("partial_result"))
    scan_timeout = int(payload.get("scan_timeout_seconds") or 0)

    if found > 0:
        payload["scan_status"] = "timeout_partial" if (timed_out or partial) else "completed_with_hits"
    elif timed_out:
        payload["scan_status"] = "timeout"
    else:
        payload["scan_status"] = "completed_no_hits"

    payload["user_message"] = _maigret_user_message(
        found_count=found,
        timed_out=timed_out,
        partial=partial,
        scan_timeout=scan_timeout,
    )
    return payload


def _load_claimed_records(report_path: Path, json_type: str) -> list[dict[str, Any]]:
    if json_type == "ndjson":
        return _load_ndjson_report(report_path)
    data = _load_json_report(report_path)
    if not data:
        return []
    return [
        {**info, "sitename": site}
        for site, info in data.items()
        if isinstance(info, dict) and _is_claimed(info)
    ]


def _latest_report_dir(username: str) -> Path | None:
    """取该用户最近一次 collect 报告目录。"""
    label = _safe_username(username)
    prefix = f"collect_{label}_"
    if not REPORTS_DIR.is_dir():
        return None
    dirs = sorted(
        (
            p
            for p in REPORTS_DIR.iterdir()
            if p.is_dir() and p.name.startswith(prefix)
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return dirs[0] if dirs else None


def _try_recover_partial_result(
    params: dict[str, Any], *, scan_timeout: int
) -> dict[str, Any] | None:
    """worker/scan 超时后从磁盘读取 maigret 已写出的部分 ndjson。"""
    username = (params.get("username") or "").strip()
    resolve_meta: dict[str, Any] = {}
    if not username and params.get("profile_url"):
        username, resolve_meta = _resolve_username("", params["profile_url"])
    if not username:
        return None

    json_format = params.get("json_format") or "ndjson"
    report_dir = _latest_report_dir(username)
    if not report_dir:
        return None

    report_path = _json_report_path(str(report_dir), username, json_format)
    if not report_path.exists():
        candidates = sorted(report_dir.glob(f"report_*_{json_format}.json"))
        report_path = candidates[0] if candidates else report_path

    if not report_path.exists():
        return None

    records = _load_claimed_records(report_path, json_format)
    if not records:
        return None

    summary = _summarize_maigret_records(records)
    found = summary.get("found_count") or 0
    return _enrich_collect_payload({
        "username": username,
        "resolve": resolve_meta,
        "scan_mode": (
            "all_sites"
            if params.get("all_sites")
            else f"top_{params.get('top_sites', 8)}"
        ),
        "json_format": json_format,
        "enable_recursion": bool(params.get("enable_recursion")),
        "timed_out": True,
        "scan_timeout_seconds": scan_timeout,
        "partial_result": True,
        "reports_root": str(REPORTS_DIR.resolve()),
        "report_dir": str(report_dir.resolve()),
        "report_json": str(report_path.resolve()),
        "report_files": _list_report_files(str(report_dir)),
        "summary": summary,
        "run": {
            "success": True,
            "returncode": None,
            "timed_out": True,
            "stderr": "",
            "stdout_tail": "",
        },
        "hint": "扫描超时但磁盘报告已有部分命中，可继续后续 MCP 采集。",
    })


def _collect_accounts_impl(
    *,
    username: str = "",
    profile_url: str = "",
    all_sites: bool = False,
    top_sites: int = 30,
    json_format: str = "ndjson",
    enable_recursion: bool = False,
    use_parse: bool = False,
    tags: str = "",
    proxy: str = "",
    cloudflare_bypass: bool = False,
    timeout: int = 180,
) -> dict[str, Any]:
    """核心收集逻辑：默认 top 30、3 分钟硬停、关闭递归。"""
    if json_format not in ("ndjson", "simple"):
        raise ValueError("json_format 仅支持 ndjson 或 simple")

    resolved_name = ""
    resolve_meta: dict[str, Any] = {}
    if not use_parse:
        resolved_name, resolve_meta = _resolve_username(username, profile_url)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = _safe_username(resolved_name or "parse")
    report_dir = REPORTS_DIR / f"collect_{label}_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    run = _run_maigret(
        [] if use_parse else [resolved_name],
        output_dir=str(report_dir),
        parse_url=profile_url if use_parse else "",
        all_sites=all_sites,
        top_sites=None if all_sites else top_sites,
        tags=tags,
        proxy=proxy,
        cloudflare_bypass=cloudflare_bypass,
        timeout=timeout,
        json_type=json_format,
        no_recursion=not enable_recursion,
        max_connections=DEFAULT_MAX_CONNECTIONS,
    )

    # 扫描可能因 Ctrl+C 或部分失败仍写出报告，优先读文件
    report_name = resolved_name or label
    report_path = _json_report_path(str(report_dir), report_name, json_format)
    if not report_path.exists():
        # --parse 或多用户名时，取目录内第一个匹配报告
        candidates = sorted(report_dir.glob(f"report_*_{json_format}.json"))
        if candidates:
            report_path = candidates[0]
            report_name = report_path.stem.replace(f"_{json_format}", "").replace("report_", "")

    records = _load_claimed_records(report_path, json_format)
    if run.get("timed_out") and not records and report_path.exists():
        records = _poll_report_records(report_path, json_format)

    summary = _summarize_maigret_records(records)
    timed_out = run.get("timed_out", False)
    partial = timed_out and bool(records)

    payload = {
        "username": report_name,
        "resolve": resolve_meta,
        "scan_mode": "all_sites" if all_sites else f"top_{top_sites}",
        "json_format": json_format,
        "enable_recursion": enable_recursion,
        "timed_out": timed_out,
        "scan_timeout_seconds": run.get("scan_timeout_seconds", timeout),
        "partial_result": partial,
        "reports_root": str(REPORTS_DIR.resolve()),
        "report_dir": str(report_dir.resolve()),
        "report_json": str(report_path.resolve()) if report_path.exists() else None,
        "report_files": _list_report_files(str(report_dir)),
        "summary": summary,
        "run": {
            "success": run["success"] or bool(records),
            "returncode": run["returncode"],
            "command": run["command"],
            "stderr": run["stderr"][-3000:] if run["stderr"] else "",
            "stdout_tail": run["stdout"][-2000:] if run["stdout"] else "",
            "timed_out": timed_out,
        },
        "hint": (
            "候选用 summary.accounts。"
            + (" 已达时间上限，返回部分结果。" if timed_out and records else "")
            + (
                " 画像任务：须与 Twitter 取证均完成后再一次性写六节。"
                if _persona_gate_enabled()
                else " 01 采集：继续步骤 3，禁止写报告。"
            )
        ),
    }
    payload = _enrich_collect_payload(payload)
    if not _persona_gate_enabled():
        payload = _attach_collect_skill_hint(payload)
    return payload


def _search_and_parse(
    username: str,
    *,
    output_dir: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """执行单次搜索并解析 JSON 报告。"""
    use_tmp = output_dir is None
    if use_tmp:
        tmp = tempfile.mkdtemp(prefix="maigret_mcp_")
        output_dir = tmp
    else:
        tmp = None

    run = _run_maigret([username], output_dir=output_dir, **kwargs)
    report_path = _json_report_path(output_dir, username)
    data = _load_json_report(report_path)
    found = _extract_claimed_accounts(data)

    result = {
        "username": username,
        "run": {
            "success": run["success"],
            "returncode": run["returncode"],
            "stderr": run["stderr"][-2000:] if run["stderr"] else "",
            "report_json": str(report_path) if report_path.exists() else None,
            "report_files": _list_report_files(output_dir),
        },
        "total_checked": len(data),
        "found_count": len(found),
        "accounts": [
            {
                "site": site,
                "url": info.get("url_user", ""),
                "status": "Claimed",
                "ids_data": _get_ids_data(info),
            }
            for site, info in found.items()
        ],
    }

    if use_tmp and tmp:
        shutil.rmtree(tmp, ignore_errors=True)

    return result


def _categorize_platforms(sites: list[str]) -> dict[str, list[str]]:
    categories: dict[str, list[str]] = {
        "社交媒体": [],
        "技术平台": [],
        "论坛社区": [],
        "其他": [],
    }
    for site in sites:
        low = site.lower()
        if any(k in low for k in SOCIAL_KEYWORDS):
            categories["社交媒体"].append(site)
        elif any(k in low for k in TECH_KEYWORDS):
            categories["技术平台"].append(site)
        elif any(k in low for k in FORUM_KEYWORDS):
            categories["论坛社区"].append(site)
        else:
            categories["其他"].append(site)
    return categories


# ── 画像成稿门禁（maigret 为取证最后一环时触发）────────────────────────

_PERSONA_DRAFT_READY_GATE: dict[str, Any] = {
    "status": "READY_TO_DRAFT",
    "structure_mode": "TWO_H1_PARTS_REQUIRED",
    "message": (
        "跨平台取证已返回。若 Twitter/OCR 等前置取证均已完成，"
        "立刻按 mandatory_output_contract 单次成稿，禁止再调工具。"
    ),
    "draft_first_line_must_be": "# 结构化预分析",
    "draft_second_h1_must_be": "# 正式人物画像报告",
    "pre_analysis_h2_locked": [
        "## 一、账号标识与资料画像",
        "## 二、账号规模与影响力",
        "## 三、地域属性与时空规律",
        "## 四、跨平台关联与联络线索",
        "## 五、内容议题与表达风格",
        "## 六、运营节奏与行为模式",
        "## 七、身份线索与关系网络",
    ],
    "formal_report_h2_locked": [
        "## 一、人物基本信息",
        "## 二、发文观点总结与立证",
        "## 三、其他平台账号与发文分析",
        "## 四、真实人物画像推断",
        "## 五、核查思路",
        "## 六、人物深度报告画像",
    ],
    "section2_rule": (
        "第二节标题须为「二、发文观点总结与立证」；"
        "从 Twitter persona 返回的 section2_citation_template 逐条粘贴立证行，"
        "禁止「二、主要人物观点与发文记录」或 1.2.3. 编号列表。"
    ),
    "section5_rule": (
        "五、核查思路：公文式段落分块说明依据；标题下直接写「关于××」，"
        "禁止「本节面向核查方/甲方」「证据来源与推理路径」「关于第五节本身」；"
        "禁止工具名与Markdown表格。"
    ),
    "draft_forbidden_first_lines": [
        "深度人物画像报告",
        "结构化预分析",
        "一、基础身份信息",
        "二、视觉与行为画像",
        "二、主要人物观点与发文记录",
    ],
    "forbidden_patterns": [
        "写「结构化预分析」但缺少行首 #",
        "预分析用自创七大点（视觉与行为/跨平台足迹/立场与倾向/风险与操控）",
        "正式报告缺「核查思路」「人物深度报告画像」或用自创六节",
        "第二节用编号列表代替「日期｜赞｜转｜原文」",
        "正文 emoji",
        "timed_out=true 却写「扫描完成/未发现账号」",
        "timed_out=false 且 found_count>0 却写「超时/无结果」",
    ],
    "maigret_interpretation": (
        "以 timed_out、summary.found_count、user_message 为准："
        "timed_out=true 如实写超时；found_count>0 须列 summary.accounts；"
        "timed_out=false 且 found_count=0 写未发现其它平台同名账号。"
    ),
}


def _suggest_handle_from_request(requested_username: str, requested_url: str) -> str:
    """扫描失败重试时，仅用本地正则（不联网）从请求参数推断 maigret 可用的 handle。"""
    name = (requested_username or "").strip().lstrip("@")
    if name and " " not in name:
        return name
    url = (requested_url or "").strip()
    if url:
        for _platform, pattern in _PROFILE_URL_PATTERNS:
            match = pattern.search(url)
            if match:
                return match.group(1)
    return ""


def _attach_persona_draft_gate(
    payload: dict[str, Any],
    *,
    primary_platform: str = "",
    primary_username: str = "",
    requested_username: str = "",
    requested_url: str = "",
) -> dict[str, Any]:
    """画像任务：maigret 返回后附成稿契约与跨平台二次采集计划。"""
    out = dict(payload)
    gate = dict(_PERSONA_DRAFT_READY_GATE)
    out["persona_draft_gate"] = gate

    try:
        hermes_home = Path(
            os.environ.get(
                "HERMES_HOME",
                Path(__file__).resolve().parents[2],
            )
        )
        scripts_dir = hermes_home / "scripts"
        if scripts_dir.is_dir() and str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from persona_cross_platform_router import plan_from_maigret_payload

        plan = plan_from_maigret_payload(
            out,
            primary_platform=(primary_platform or "twitter").strip().lower(),
            primary_username=(primary_username or out.get("username") or "").strip(),
        )
        out["cross_platform_collection_plan"] = plan
        if plan.get("collect"):
            actions = []
            for it in plan["collect"]:
                steps = it.get("collect_steps")
                if isinstance(steps, list) and steps:
                    for s in steps:
                        tool = s.get("tool") or ""
                        args = s.get("suggested_args") or {}
                        actions.append(f"调 {tool} 参数 {args}")
                else:
                    actions.append(
                        it.get("instruction")
                        or f"采集 {it.get('platform')}: {it.get('account_url')}"
                    )
            out["NEXT_ACTIONS_REQUIRED"] = actions
            out["DRAFT_BLOCKED"] = True
            out["DRAFT_BLOCKED_REASON"] = (
                f"maigret 发现 {len(actions)} 个其他平台账号，必须逐个采集后才能成稿："
                + "；".join(actions)
            )
            gate["status"] = "MAIGRET_DONE_COLLECT_OTHER_PLATFORMS"
            gate["message"] = (
                "maigret 已完成；禁止立即成稿。"
                "必须按 NEXT_ACTIONS_REQUIRED 逐条采集其它平台（主页+发文）。"
                "有 MCP 用 MCP；无 MCP 用 Apify + firecrawl + web_search（三步须全做，入库 persona_social_*）。"
                "禁止空参数、禁止漏采、禁止重复采集主平台。"
            )
            pending = list(gate.get("pending_tools_before_draft") or [])
            for a in actions:
                pending.append(a)
            gate["pending_tools_before_draft"] = pending
            gate["draft_forbidden_until_cross_platform_collected"] = True
            out["persona_draft_gate"] = gate
    except Exception:
        pass

    # 扫描失败（如传入无法解析的主页 URL）→ 禁止直接成稿，要求按用户名重试
    scan_status = str(out.get("scan_status") or "")
    has_collect = bool(out.get("cross_platform_collection_plan", {}).get("collect"))
    scan_failed = (not has_collect) and (
        scan_status == "failed" or bool(out.get("error"))
    )
    if scan_failed:
        suggested = _suggest_handle_from_request(
            requested_username or primary_username, requested_url
        )
        retry_arg = (
            f"collect_accounts(username=\"{suggested}\", primary_platform=\"{primary_platform or 'twitter'}\")"
            if suggested
            else "collect_accounts(username=\"<目标用户名，不要带空格/不要用主页URL>\")"
        )
        gate["status"] = "MAIGRET_SCAN_FAILED_RETRY"
        gate["message"] = (
            "maigret 跨平台扫描失败（多因传入了无法解析的主页 URL）。"
            f"禁止直接成稿、禁止凭空声称已采集任何平台。请改用用户名重试：{retry_arg}。"
            "maigret 按用户名扫描，不要传 profile_url 或带空格的显示名。"
        )
        out["DRAFT_BLOCKED"] = True
        out["DRAFT_BLOCKED_REASON"] = (
            "maigret 扫描失败，跨平台账号未确认；必须先用用户名重试 collect_accounts，"
            f"再完成各平台主页+发文采集后方可成稿。建议重试：{retry_arg}"
        )
        out["NEXT_ACTIONS_REQUIRED"] = [retry_arg]
        gate["draft_forbidden_until_cross_platform_collected"] = True
        out["persona_draft_gate"] = gate

    return out


def _persona_draft_result_banner() -> str:
    return (
        "【画像成稿门禁 / 取证已完成】\n"
        "第一行：# 结构化预分析（必须有#）\n"
        "预分析七节标题固定：账号标识与资料画像/账号规模与影响力/地域属性与时空规律/"
        "跨平台关联与联络线索/内容议题与表达风格/运营节奏与行为模式/身份线索与关系网络\n"
        "然后：# 正式人物画像报告\n"
        "正式六节固定：人物基本信息/发文观点总结与立证/其他平台账号与发文分析/"
        "真实人物画像推断/核查思路/人物深度报告画像\n"
        "第二节：粘贴 section2_citation_template 中的立证行，禁止「主要人物观点与发文记录」\n"
        "---JSON_BELOW---\n"
    )


# ── MCP 工具 ──────────────────────────────────────────────────────────

@mcp.tool()
async def collect_accounts(
    username: str = "",
    profile_url: str = "",
    all_sites: bool = False,
    top_sites: int = 10,
    json_format: str = "ndjson",
    enable_recursion: bool = False,
    use_parse: bool = False,
    tags: str = "",
    proxy: str = "",
    cloudflare_bypass: bool = False,
    timeout: int = 150,
    primary_platform: str = "twitter",
    primary_username: str = "",
) -> dict[str, Any]:
    """
    【01 采集 · 步骤 2】跨平台用户名扫描，返回候选账号列表（summary.accounts）。

    仅作候选发现，禁止根据本工具返回写报告或画像。
    下一步由 Skill 步骤 3 对各候选采主页（MCP 或 Apify）。

    参数: username（纯用户名，如 whyyoutouzhele）、top_sites 默认 10、timeout 默认 150。
    enable_recursion 必须 false。不要传带空格的用户名或 profile_url 代替 username。
    """
    params = {
        "username": username,
        "profile_url": profile_url,
        "all_sites": all_sites,
        "top_sites": top_sites,
        "json_format": json_format,
        "enable_recursion": enable_recursion,
        "use_parse": use_parse,
        "tags": tags,
        "proxy": proxy,
        "cloudflare_bypass": cloudflare_bypass,
        "timeout": timeout,
    }
    payload = await _collect_accounts_via_worker(params)
    if _persona_gate_enabled():
        payload = _attach_persona_draft_gate(
            payload,
            primary_platform=primary_platform,
            primary_username=primary_username or username,
            requested_username=username,
            requested_url=profile_url,
        )
    elif isinstance(payload, dict):
        payload = _attach_collect_skill_hint(payload)
    payload["scan_scope_note"] = (
        f"本次扫描 top_{top_sites} 高优先级站点（非全库 3000+）。"
        "全量请单独调用 search_username(top_sites=50~100) 或 collect_accounts(all_sites=true, timeout≥600)。"
    )
    return payload


@mcp.tool()
def search_username(
    username: str,
    top_sites: int = 50,
    all_sites: bool = False,
    tags: str = "",
    exclude_tags: str = "",
    keywords: list[str] | None = None,
    sites: list[str] | None = None,
    proxy: str = "",
    cloudflare_bypass: bool = False,
    with_domains: bool = False,
    timeout: int = 300,
) -> dict[str, Any]:
    """
    在 3000+ 网站按用户名搜索账号（轻量，返回 simple JSON 摘要）。

    - top_sites: 默认 50（约 2-4 分钟）；深度可 100-500
    - all_sites: 为 true 时查全部站点（极慢，勿用）
    - collect_accounts 失败时不要换本工具重复扫同一用户；检查代理与 timeout
    - tags: 按标签过滤，如 "photo,dating" 或 "cn"
    - exclude_tags: 排除标签
    - keywords: 页面关键词高亮，如 ["python", "rust"]
    - sites: 限定站点列表
    - proxy: HTTP/SOCKS 代理，如 socks5://127.0.0.1:1080
    - cloudflare_bypass: 启用 Cloudflare 绕过（需本地 FlareSolverr）
    - with_domains: 实验性域名检查
    """
    return _search_and_parse(
        username,
        top_sites=None if all_sites else top_sites,
        all_sites=all_sites,
        tags=tags,
        exclude_tags=exclude_tags,
        keywords=keywords,
        sites=sites,
        proxy=proxy,
        cloudflare_bypass=cloudflare_bypass,
        with_domains=with_domains,
        timeout=timeout,
        no_recursion=True,
        json_type="simple",
    )


@mcp.tool()
def search_usernames(
    usernames: list[str],
    top_sites: int = 500,
    all_sites: bool = False,
    tags: str = "",
    proxy: str = "",
    timeout: int = 300,
) -> dict[str, Any]:
    """批量搜索多个用户名（maigret 支持一次传入多个 username）。"""
    output_dir = tempfile.mkdtemp(prefix="maigret_batch_")
    try:
        run = _run_maigret(
            usernames,
            output_dir=output_dir,
            top_sites=None if all_sites else top_sites,
            all_sites=all_sites,
            tags=tags,
            proxy=proxy,
            timeout=timeout,
        )
        results = []
        for name in usernames:
            data = _load_json_report(_json_report_path(output_dir, name))
            found = _extract_claimed_accounts(data)
            results.append({
                "username": name,
                "found_count": len(found),
                "platforms": list(found.keys()),
                "accounts": [
                    {"site": s, "url": i.get("url_user", "")}
                    for s, i in found.items()
                ],
            })
        return {
            "usernames": usernames,
            "run": {
                "success": run["success"],
                "returncode": run["returncode"],
                "stderr": run["stderr"][-2000:] if run["stderr"] else "",
            },
            "results": results,
        }
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


@mcp.tool()
def extract_profile_info(
    username: str,
    top_sites: int = 500,
    proxy: str = "",
    timeout: int = 300,
) -> dict[str, Any]:
    """搜索用户名并从各平台页面提取 PII（姓名、邮箱、地区、简介、头像等）。"""
    parsed = _search_and_parse(username, top_sites=top_sites, proxy=proxy, timeout=timeout)
    accounts = {
        a["site"]: {"url_user": a["url"], "status": {"ids_data": a["ids_data"]}}
        for a in parsed.get("accounts", [])
    }
    pii = _extract_pii(accounts)
    return {
        "username": username,
        "found_accounts": parsed.get("found_count", 0),
        "personal_info": pii,
        "platforms": list(accounts.keys()),
        "run": parsed.get("run"),
    }


@mcp.tool()
def recursive_search(
    username: str,
    top_sites: int = 200,
    max_usernames: int = 3,
    proxy: str = "",
    timeout: int = 300,
) -> dict[str, Any]:
    """递归追查：从账号页发现新用户名后继续搜索（多马甲追踪）。"""
    queue = [username]
    visited: set[str] = set()
    all_results: dict[str, Any] = {}

    while queue and len(visited) < max_usernames:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        parsed = _search_and_parse(
            current,
            top_sites=top_sites,
            proxy=proxy,
            timeout=timeout,
        )
        accounts = {
            a["site"]: {"url_user": a["url"], "status": {"ids_data": a["ids_data"]}}
            for a in parsed.get("accounts", [])
        }
        pii = _extract_pii(accounts)
        discovered = pii.get("extra_usernames", [])

        all_results[current] = {
            "found_count": parsed.get("found_count", 0),
            "platforms": list(accounts.keys()),
            "accounts": [
                {"site": s, "url": i.get("url_user", "")}
                for s, i in accounts.items()
            ],
            "discovered_usernames": discovered,
        }
        for new_u in discovered:
            if new_u not in visited and new_u not in queue:
                queue.append(new_u)

    return {
        "seed_username": username,
        "total_usernames_checked": len(visited),
        "all_usernames": list(visited),
        "results": all_results,
    }


@mcp.tool()
def extract_url_ids(
    url: str,
    request_timeout: int = 30,
) -> dict[str, Any]:
    """
    快速解析个人主页 URL，仅提取页面中的用户名/ID（约 10-60 秒）。

    不做全站扫描。若需反查关联账号，请对提取出的用户名调用 search_username。
  """
    result = _extract_ids_from_url(url, request_timeout=request_timeout)
    return {
        "url": url,
        **result,
        "hint": "提取完成后可对 usernames 列表调用 search_username(top_sites=100)",
    }


@mcp.tool()
def parse_profile_url(
    url: str,
    search_after_parse: bool = False,
    top_sites: int = 100,
    proxy: str = "",
    timeout: int = 600,
    request_timeout: int = 30,
) -> dict[str, Any]:
    """
    解析个人主页 URL（maigret --parse）。

    - search_after_parse=false（默认）：仅提取 ID/用户名，约 1 分钟内完成
    - search_after_parse=true：提取后再对用户名做 top_sites 全站搜索（较慢，勿并行多个）
    """
    extracted = _extract_ids_from_url(url, request_timeout=request_timeout)
    result: dict[str, Any] = {
        "parse_url": url,
        "search_after_parse": search_after_parse,
        "extraction": extracted,
    }

    if not search_after_parse:
        result["hint"] = (
            "已完成 URL 解析。如需反查关联账号，设 search_after_parse=true "
            "或对 extraction.usernames 调用 search_username"
        )
        return result

    usernames = extracted.get("usernames") or []
    if not usernames:
        result["search"] = {
            "skipped": True,
            "reason": "页面未提取到用户名，无法继续全站搜索",
        }
        return result

    # 全站搜索：保存到持久报告目录，避免临时目录被删
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    primary = usernames[0]
    report_dir = REPORTS_DIR / f"parse_{_safe_username(primary)}_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    run = _run_maigret(
        usernames,
        output_dir=str(report_dir),
        parse_url=url,
        top_sites=top_sites,
        proxy=proxy,
        timeout=timeout,
        json_type="simple",
        html=False,
    )

    reports = list(report_dir.glob("report_*_simple.json"))
    all_found: list[dict[str, Any]] = []
    for rp in reports:
        data = _load_json_report(rp)
        found = _extract_claimed_accounts(data)
        for site, info in found.items():
            all_found.append({
                "site": site,
                "url": info.get("url_user", ""),
                "source_report": rp.name,
            })

    result["search"] = {
        "usernames_searched": usernames,
        "found_count": len(all_found),
        "accounts": all_found,
        "report_dir": str(report_dir.resolve()),
        "report_files": _list_report_files(str(report_dir)),
        "run": {
            "success": run["success"],
            "returncode": run["returncode"],
            "stderr": run["stderr"][-2000:] if run["stderr"] else "",
        },
    }
    return result


@mcp.tool()
def permute_search(
    name_parts: list[str],
    top_sites: int = 300,
    proxy: str = "",
    timeout: int = 300,
) -> dict[str, Any]:
    """
    用户名变体搜索（maigret --permute）。

    例: name_parts=["john", "doe"] → 搜索 johndoe、j.doe 等变体。
    """
    if len(name_parts) < 2:
        raise ValueError("permute_search 至少需要 2 个名字片段")
    output_dir = tempfile.mkdtemp(prefix="maigret_permute_")
    try:
        run = _run_maigret(
            name_parts,
            output_dir=output_dir,
            permute=True,
            top_sites=top_sites,
            proxy=proxy,
            timeout=timeout,
        )
        reports = list(Path(output_dir).glob("report_*_simple.json"))
        results = []
        for rp in reports:
            # report_john_doe_simple.json → 还原用户名较复杂，用文件名
            data = _load_json_report(rp)
            found = _extract_claimed_accounts(data)
            results.append({
                "report": rp.name,
                "found_count": len(found),
                "platforms": list(found.keys()),
            })
        return {
            "name_parts": name_parts,
            "permute": True,
            "run": {
                "success": run["success"],
                "returncode": run["returncode"],
                "stderr": run["stderr"][-2000:] if run["stderr"] else "",
            },
            "variant_results": results,
        }
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


@mcp.tool()
def generate_report(
    username: str,
    top_sites: int = 100,
    formats: list[str] | None = None,
    proxy: str = "",
    timeout: int = 900,
) -> dict[str, Any]:
    """
    搜索并生成调查报告文件（单次调用约 3-15 分钟，勿与多个 maigret 任务并行）。

    formats 可选: html, pdf, csv, txt, json, graph, xmind, md（默认 html+json）
    HTML 文件名为 report_{username}_plain.html，保存在 MAIGRET_REPORTS_DIR 子目录。
    """
    fmt = formats or ["html", "json"]
    fmt_set = {f.lower() for f in fmt}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORTS_DIR / f"{_safe_username(username)}_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    run = _run_maigret(
        [username],
        output_dir=str(report_dir),
        top_sites=top_sites,
        proxy=proxy,
        timeout=timeout,
        html="html" in fmt_set,
        pdf="pdf" in fmt_set,
        csv="csv" in fmt_set,
        txt="txt" in fmt_set,
        graph="graph" in fmt_set,
        xmind="xmind" in fmt_set,
        markdown="md" in fmt_set,
        json_type="simple",
    )

    files = _list_report_files(str(report_dir))
    data = _load_json_report(_json_report_path(str(report_dir), username))
    found = _extract_claimed_accounts(data)
    html_path = _find_html_report(str(report_dir), username)

    return {
        "username": username,
        "found_count": len(found),
        "platforms_found": list(found.keys()),
        "reports_root": str(REPORTS_DIR.resolve()),
        "report_dir": str(report_dir.resolve()),
        "report_html": html_path,
        "report_json": str(_json_report_path(str(report_dir), username).resolve())
        if _json_report_path(str(report_dir), username).exists() else None,
        "report_files": files,
        "run": {
            "success": run["success"],
            "returncode": run["returncode"],
            "stderr": run["stderr"][-2000:] if run["stderr"] else "",
            "stdout_tail": run["stdout"][-1500:] if run["stdout"] else "",
        },
    }


@mcp.tool()
def cross_platform_analysis(
    username: str,
    top_sites: int = 500,
    recursive: bool = False,
    max_usernames: int = 3,
    proxy: str = "",
    timeout: int = 300,
) -> dict[str, Any]:
    """跨平台综合分析：汇总账号、提取 PII、输出结构化人物画像。"""
    if recursive:
        rec = recursive_search(
            username=username,
            top_sites=top_sites,
            max_usernames=max_usernames,
            proxy=proxy,
            timeout=timeout,
        )
        all_accounts: list[dict[str, Any]] = []
        merged_pii: dict[str, Any] = {
            "names": set(), "emails": set(), "phones": set(),
            "locations": set(), "bio": set(), "avatars": [],
            "urls": [], "extra_usernames": set(),
        }
        for uname, block in rec.get("results", {}).items():
            for acc in block.get("accounts", []):
                all_accounts.append({**acc, "username_used": uname})
        parsed = _search_and_parse(username, top_sites=top_sites, proxy=proxy, timeout=timeout)
        accounts_map = {
            a["site"]: {"url_user": a["url"], "status": {"ids_data": a["ids_data"]}}
            for a in parsed.get("accounts", [])
        }
        pii = _extract_pii(accounts_map)
        for field in ("names", "emails", "phones", "locations", "bio", "extra_usernames"):
            merged_pii[field].update(pii.get(field, []))
        merged_pii["avatars"].extend(pii.get("avatars", []))
        merged_pii["urls"].extend(pii.get("urls", []))
        sites = [a["site"] for a in all_accounts]
    else:
        parsed = _search_and_parse(username, top_sites=top_sites, proxy=proxy, timeout=timeout)
        all_accounts = [
            {"site": a["site"], "url": a["url"], "username_used": username}
            for a in parsed.get("accounts", [])
        ]
        accounts_map = {
            a["site"]: {"url_user": a["url"], "status": {"ids_data": a["ids_data"]}}
            for a in parsed.get("accounts", [])
        }
        merged_pii = _extract_pii(accounts_map)
        sites = list(accounts_map.keys())
        rec = None

    categories = _categorize_platforms(sites)
    return {
        "target_username": username,
        "recursive": recursive,
        "recursive_detail": rec,
        "summary": {
            "total_platforms": len(all_accounts),
            "platform_breakdown": {k: len(v) for k, v in categories.items()},
        },
        "personal_info": {
            k: sorted(v) if isinstance(v, set) else v
            for k, v in merged_pii.items()
        },
        "platform_categories": categories,
        "all_accounts": all_accounts,
    }


@mcp.tool()
def ai_investigation(
    username: str,
    top_sites: int = 500,
    ai_model: str = "",
    proxy: str = "",
    timeout: int = 600,
) -> dict[str, Any]:
    """
    AI 辅助调查摘要（maigret --ai）。

    需要环境变量 OPENAI_API_KEY，或 maigret settings.json 中的 openai_api_key。
    支持 OpenAI 兼容 API（OpenRouter、Azure、本地 vLLM 等）。
    """
    output_dir = tempfile.mkdtemp(prefix="maigret_ai_")
    try:
        run = _run_maigret(
            [username],
            output_dir=output_dir,
            top_sites=top_sites,
            proxy=proxy,
            timeout=timeout,
            ai=True,
            ai_model=ai_model,
        )
        return {
            "username": username,
            "ai_analysis": run["stdout"],
            "run": {
                "success": run["success"],
                "returncode": run["returncode"],
                "stderr": run["stderr"][-2000:] if run["stderr"] else "",
            },
        }
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


@mcp.tool()
def database_stats() -> dict[str, Any]:
    """查看 maigret 站点数据库统计（maigret --stats）。"""
    output_dir = tempfile.mkdtemp(prefix="maigret_stats_")
    try:
        run = _run_maigret(
            ["_stats_probe_"],
            output_dir=output_dir,
            top_sites=1,
            extra_args=["--stats"],
            timeout=120,
        )
        return {
            "stats_output": run["stdout"],
            "stderr": run["stderr"][-1000:] if run["stderr"] else "",
            "returncode": run["returncode"],
            "success": run["success"],
        }
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)


if __name__ == "__main__":
    mcp.run()
