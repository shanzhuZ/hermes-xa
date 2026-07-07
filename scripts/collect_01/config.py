"""读取 HERMES_HOME/.env 中的 MySQL 连接配置。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict


def _repo_root() -> Path:
    """scripts/collect_01/config.py → 仓库根目录。"""
    return Path(__file__).resolve().parent.parent.parent


def hermes_home() -> Path:
    val = os.environ.get("HERMES_HOME", "").strip()
    if val:
        return Path(val).resolve()
    # Hook 子进程常未继承 HERMES_HOME，回退到本仓库根（含 config.yaml / .env）
    return _repo_root()


def load_env() -> None:
    env_path = hermes_home() / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
        _load_env_fallback(env_path)


def _load_env_fallback(env_path: Path) -> None:
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def db_config() -> Dict[str, Any]:
    load_env()
    return {
        "host": os.environ.get("HERMES_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("HERMES_DB_PORT", "3306")),
        "user": os.environ.get("HERMES_DB_USER", "root"),
        "password": os.environ.get("HERMES_DB_PASSWORD", "123456"),
        "database": os.environ.get("HERMES_DB_NAME", "hermes-xa"),
        "charset": "utf8mb4",
        "cursorclass": None,
    }


def persist_enabled() -> bool:
    load_env()
    flag = os.environ.get("HERMES_PERSIST_ENABLED", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}
