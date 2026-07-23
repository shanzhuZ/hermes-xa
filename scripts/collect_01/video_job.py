"""01：平台发文完成后，短触发后台视频分析（防 Hook/MCP 超时）。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional  # noqa: F401 — Any 用于 _video_python 扫描

from collect_01 import db
from collect_01.gates import get_step_status
from collect_01.phases import (
    post_step_key,
    video_step_key,
    video_step_node,
    video_step_order,
    video_step_title,
)
from collect_01.video_select import select_latest_downloadable_video

logger = logging.getLogger(__name__)

# 墙钟超时（秒）：与方案约定一致
VIDEO_WALL_TIMEOUT_SEC = int(os.environ.get("HERMES_COLLECT_VIDEO_TIMEOUT_SEC", "600"))


def _video_python() -> str:
    """与 config.yaml video2frame 一致：必须用带 yt-dlp/cv2/Pillow 的解释器。

    禁止回落到 hermes-agent venv（其 Pillow 常损坏，会报 cannot import _imaging）。
    """
    env = (os.environ.get("HERMES_VIDEO_PYTHON") or "").strip()
    if env and Path(env).is_file():
        return env

    # 从 config.yaml 扫描 video2frame.command
    try:
        import yaml

        cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        if cfg_path.is_file():
            data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

            def _find_cmd(obj: Any) -> Optional[str]:
                if isinstance(obj, dict):
                    args = obj.get("args") or []
                    if obj.get("command") and any("video2frame" in str(a) for a in args):
                        return str(obj["command"])
                    for v in obj.values():
                        found = _find_cmd(v)
                        if found:
                            return found
                elif isinstance(obj, list):
                    for it in obj:
                        found = _find_cmd(it)
                        if found:
                            return found
                return None

            found = _find_cmd(data)
            if found and Path(found).is_file():
                return found
    except Exception as exc:
        logger.debug("读取 video2frame python 失败: %s", exc)

    for candidate in (
        Path(r"D:/environment/python/python.exe"),
        Path(r"C:/environment/python/python.exe"),
    ):
        if candidate.is_file():
            return str(candidate)

    exe = sys.executable
    if "hermes-agent" in exe.replace("\\", "/").lower():
        logger.warning(
            "视频分析将使用 hermes-agent venv，Pillow/OpenCV 可能不可用: %s",
            exe,
        )
    return exe


def _scripts_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _apply_video2frame_env_from_config(env: Dict[str, str]) -> None:
    """从 config.yaml video2frame.env 注入未设置的变量（如 cookies / proxy）。"""
    try:
        import yaml

        cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        if not cfg_path.is_file():
            return
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        node = (data.get("mcp_servers") or {}).get("video2frame") or {}
        for k, v in (node.get("env") or {}).items():
            if v is None:
                continue
            key = str(k)
            val = str(v)
            # 跳过未展开的 ${VAR}
            if val.startswith("${") and val.endswith("}"):
                ref = val[2:-1]
                val = env.get(ref) or os.environ.get(ref) or ""
                if not val:
                    continue
            if key and val and not env.get(key):
                env[key] = val
    except Exception as exc:
        logger.debug("注入 video2frame env 失败: %s", exc)


def _isolated_video_env(scripts: Path) -> Dict[str, str]:
    """隔离子进程环境，避免 Hook/venv 的 PYTHONPATH 污染系统 Python 的 Pillow。"""
    env = {k: v for k, v in os.environ.items() if isinstance(v, str)}
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    env["HERMES_HOME"] = env.get("HERMES_HOME") or str(scripts.parent)
    # 只保留业务模块路径，绝不继承父进程 PYTHONPATH
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(scripts),
            str(scripts.parent / "mcp" / "servers" / "video2frame-mcp"),
        ]
    )
    for key in (
        "VIRTUAL_ENV",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
    ):
        env.pop(key, None)
    path_parts = [p for p in (env.get("PATH") or "").split(os.pathsep) if p]
    cleaned = []
    for p in path_parts:
        norm = p.replace("\\", "/").lower()
        if "hermes-agent" in norm and "/venv/" in norm:
            continue
        cleaned.append(p)
    if cleaned:
        env["PATH"] = os.pathsep.join(cleaned)
    _apply_video2frame_env_from_config(env)
    return env


def ensure_video_step(store, task_id: str, platform: str) -> str:
    """确保 6.x.1 节点存在，父节点为 step6_post_{platform}。"""
    parent = post_step_key(platform)
    store.ensure_post_steps(task_id, [platform])
    key = video_step_key(platform)
    db.execute(
        """
        INSERT IGNORE INTO collect_phase_steps
          (task_id, step_key, parent_step_key, step_order, step_node, title, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'pending')
        """,
        (
            task_id,
            key,
            parent,
            video_step_order(platform),
            video_step_node(platform),
            video_step_title(platform),
        ),
    )
    return key


def maybe_start_platform_video(store, task_id: str, platform: str) -> Optional[Dict[str, Any]]:
    """发文子节点完成后：有可下载视频则建 6.x.1 并后台跑；无则不建。

    必须在数秒内返回，禁止同步跑管线。
    """
    if not task_id or not platform:
        return None
    platform = str(platform).strip().lower()
    key = video_step_key(platform)
    existing = get_step_status(task_id, key)
    # failed 允许重试；其余终态/进行中不重复拉起
    if existing in {"pending", "running", "completed", "skipped"}:
        logger.info("视频节点已存在 task=%s platform=%s status=%s", task_id, platform, existing)
        return {"skipped": True, "reason": "already_exists", "step_key": key, "status": existing}
    if existing == "failed":
        logger.info("视频节点曾失败，准备重试 task=%s platform=%s", task_id, platform)

    picked = select_latest_downloadable_video(task_id, platform)
    if not picked or not picked.get("url"):
        logger.info("平台无下载视频，不建节点 task=%s platform=%s", task_id, platform)
        return {"skipped": True, "reason": "no_video"}

    ensure_video_step(store, task_id, platform)
    store.set_step_status(
        task_id,
        key,
        "running",
        message=f"后台分析最新视频 {picked.get('post_id') or ''}".strip(),
        payload={
            "origin_url": picked["url"],
            "post_id": picked.get("post_id"),
            "account_id": picked.get("account_id"),
            "wall_timeout_sec": VIDEO_WALL_TIMEOUT_SEC,
        },
    )
    spawned = _spawn_video_runner(
        task_id=task_id,
        platform=platform,
        origin_url=str(picked["url"]),
        account_id=str(picked.get("account_id") or ""),
        post_id=str(picked.get("post_id") or ""),
    )
    logger.info(
        "已拉起视频后台任务 task=%s platform=%s url=%s pid=%s",
        task_id,
        platform,
        picked["url"][:120],
        spawned.get("pid"),
    )
    return {"started": True, "step_key": key, "pick": picked, **spawned}


def _spawn_video_runner(
    *,
    task_id: str,
    platform: str,
    origin_url: str,
    account_id: str,
    post_id: str,
) -> Dict[str, Any]:
    py = _video_python()
    scripts = _scripts_dir()
    log_dir = Path(os.environ.get("HERMES_HOME") or scripts.parent) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"video_job_{task_id[:8]}_{platform}.log"

    cmd = [
        py,
        "-m",
        "collect_01.video_runner",
        "--task-id",
        task_id,
        "--platform",
        platform,
        "--origin-url",
        origin_url,
        "--account-id",
        account_id,
        "--post-id",
        post_id,
        "--timeout-sec",
        str(VIDEO_WALL_TIMEOUT_SEC),
    ]
    env = _isolated_video_env(scripts)
    logger.info("视频后台 python=%s PYTHONPATH=%s", py, env.get("PYTHONPATH"))

    popen_kwargs: Dict[str, Any] = {
        "cwd": str(scripts),
        "env": env,
        "stdout": open(log_path, "a", encoding="utf-8"),
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        # 脱离 Hook 进程，避免 120s 杀掉子进程
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        popen_kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        popen_kwargs["close_fds"] = False
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    return {"pid": proc.pid, "log": str(log_path)}
