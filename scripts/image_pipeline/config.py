"""读取 HERMES_HOME/.env 中的 MySQL / HBase / 下载配置。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict


def _repo_root() -> Path:
    # scripts/image_pipeline/config.py → 仓库根
    return Path(__file__).resolve().parent.parent.parent


def hermes_home() -> Path:
    val = os.environ.get("HERMES_HOME", "").strip()
    if val:
        return Path(val).resolve()
    return _repo_root()


def load_env() -> None:
    env_path = hermes_home() / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
    except ImportError:
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
    }


def persist_enabled() -> bool:
    load_env()
    flag = os.environ.get("HERMES_PERSIST_ENABLED", "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def hbase_config() -> Dict[str, Any]:
    """
    图片 HBase 配置。

    Python 写入：现成 HTTP 接口 insertHbaseData（与「图片hbase入库.py」一致）
    Java 读取：ZK 原生客户端（application.yml hermes.hbase.zookeeper）

    注意：ZK 必须指向 insertHbaseData 实际落库的那个集群。
    """
    load_env()
    return {
        "enabled": os.environ.get("HERMES_HBASE_ENABLED", "0").strip().lower()
        not in {"0", "false", "no", "off"},
        "insert_url": os.environ.get(
            "HERMES_HBASE_INSERT_URL",
            "http://192.168.3.171:6666/insertHbaseData",
        ).strip(),
        "table": os.environ.get("HERMES_HBASE_IMAGE_TABLE", "collect_image_bytes").strip()
        or "collect_image_bytes",
        # 与现成接口 / 历史配置一致：列族 info
        "column_family": os.environ.get("HERMES_HBASE_COLUMN_FAMILY", "info").strip()
        or "info",
        "timeout_ms": int(os.environ.get("HERMES_HBASE_TIMEOUT_MS", "30000")),
        # Java 侧 ZK（仅作文档式配置，Python 写不走 ZK）
        "zk": os.environ.get("HERMES_HBASE_ZK", "192.168.3.171").strip() or "192.168.3.171",
        "zk_port": int(os.environ.get("HERMES_HBASE_ZK_PORT", "2181")),
        "local_fallback_dir": os.environ.get(
            "HERMES_IMAGE_LOCAL_DIR",
            str(hermes_home() / "data" / "image_bytes"),
        ),
    }


def download_config() -> Dict[str, Any]:
    load_env()
    return {
        "connect_timeout": float(os.environ.get("HERMES_IMAGE_CONNECT_TIMEOUT", "10")),
        "read_timeout": float(os.environ.get("HERMES_IMAGE_READ_TIMEOUT", "60")),
        "max_bytes": int(os.environ.get("HERMES_IMAGE_MAX_BYTES", str(20 * 1024 * 1024))),
        "max_retries": int(os.environ.get("HERMES_IMAGE_MAX_RETRIES", "3")),
        "user_agent": os.environ.get(
            "HERMES_IMAGE_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        ),
    }
