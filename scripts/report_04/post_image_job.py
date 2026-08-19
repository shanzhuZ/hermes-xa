# -*- coding: utf-8 -*-
"""发文后第二次图片管线：独立进程，超时主动杀掉 image_pipeline 子进程。

仅用于步骤7→8 补扫帖子配图。4.1.2 第一次核验仍走 stream_steps.start_step5_image_pipeline，
不得改用本入口。

用法：
  python -m report_04.post_image_job --task-id <id> --timeout-sec 90 --skip-if-stored
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_REPO = _SCRIPTS.parent
if not (os.environ.get("HERMES_HOME") or "").strip():
    os.environ["HERMES_HOME"] = str(_REPO)

logger = logging.getLogger("report_04.post_image_job")


def _setup_logging() -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    log_dir = Path(os.environ.get("HERMES_HOME") or _REPO) / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    handlers = [logging.StreamHandler(sys.stderr)]
    try:
        handlers.append(
            logging.FileHandler(log_dir / "post_image_job.log", encoding="utf-8")
        )
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [post_image_job] %(message)s",
        handlers=handlers,
    )


def _kill_process(proc: subprocess.Popen) -> None:
    """超时后杀掉 image_pipeline 子进程（含 Windows）。"""
    if proc.poll() is not None:
        return
    try:
        proc.kill()
    except Exception as exc:
        logger.warning("kill image_pipeline 失败 pid=%s: %s", proc.pid, exc)
        return
    try:
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main(argv: list | None = None) -> int:
    _setup_logging()
    parser = argparse.ArgumentParser(description="04 发文后第二次图片管线")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--timeout-sec", type=int, default=90)
    parser.add_argument(
        "--skip-if-stored",
        action="store_true",
        help="已有 stored 图则直接退出（与原 7→8 调用一致）",
    )
    args = parser.parse_args(argv)
    task_id = str(args.task_id or "").strip()
    timeout_sec = int(args.timeout_sec or 90)
    if timeout_sec <= 0:
        timeout_sec = 90
    if not task_id:
        logger.error("缺少 --task-id")
        return 2

    if args.skip_if_stored:
        try:
            from collect_01.image_assets import count_stored_images

            n = int(count_stored_images(task_id) or 0)
        except Exception as exc:
            logger.warning("count_stored_images 失败 task=%s: %s", task_id, exc)
            n = 0
        if n > 0:
            logger.info("已有 stored 图 %s 张，第二次管线跳过 task=%s", n, task_id)
            return 0

    env = os.environ.copy()
    prev = (env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = (
        str(_SCRIPTS) if not prev else f"{_SCRIPTS}{os.pathsep}{prev}"
    )
    cmd = [
        sys.executable,
        "-m",
        "image_pipeline.run",
        "--task-id",
        task_id,
        "--force-analyze",
    ]
    logger.info(
        "启动 image_pipeline.run task=%s timeout=%ss",
        task_id,
        timeout_sec,
    )
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_SCRIPTS),
            env=env,
        )
    except Exception as exc:
        logger.warning("拉起 image_pipeline.run 失败 task=%s: %s", task_id, exc)
        return 1
    try:
        rc = proc.wait(timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        logger.warning(
            "第二次图片管线超时(%ss)，主动结束子进程 pid=%s task=%s",
            timeout_sec,
            proc.pid,
            task_id,
        )
        _kill_process(proc)
        return 2
    logger.info("image_pipeline.run 结束 task=%s rc=%s", task_id, rc)
    return int(rc or 0)


if __name__ == "__main__":
    raise SystemExit(main())
