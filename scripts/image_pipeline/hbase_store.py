"""
HBase 原图写入 / 本地回退。

写入对齐现成 HTTP 接口（见「图片hbase入库.py」）：
  POST http://192.168.3.171:6666/insertHbaseData
  body = {tableName, rowKey, data}
  data = JSON字符串 {"image_url": "...", "base64_data": "data:image/xxx;base64,..."}

说明：
- row_key 由本模块生成：img:{taskId}:{sha256前16}
- 元数据（mime/size/sha256）只在 MySQL；HBase 只存接口约定的 data JSON
- HERMES_HBASE_ENABLED=0 时回退本地目录
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from image_pipeline.config import hbase_config

logger = logging.getLogger(__name__)


class HBaseStoreError(RuntimeError):
    pass


def build_row_key(task_id: str, sha256: str) -> str:
    """RowKey: img:{taskId}:{sha256前16}"""
    return f"img:{task_id}:{sha256[:16]}"


def _local_path(row_key: str, base_dir: str) -> Path:
    safe = row_key.replace(":", "_")
    return Path(base_dir) / f"{safe}.bin"


def _write_local(row_key: str, payload: Dict[str, Any], base_dir: str) -> None:
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = _local_path(row_key, base_dir)
    meta_path = path.with_suffix(".meta.txt")
    path.write_bytes(payload["bytes"])
    meta_path.write_text(
        "\n".join(
            [
                f"mime={payload.get('mime_type') or ''}",
                f"url={payload.get('origin_url') or ''}",
                f"sha256={payload.get('sha256') or ''}",
                f"file_size={payload.get('file_size') or 0}",
            ]
        ),
        encoding="utf-8",
    )


def _read_local(row_key: str, base_dir: str) -> Optional[Dict[str, Any]]:
    path = _local_path(row_key, base_dir)
    if not path.is_file():
        return None
    data = path.read_bytes()
    mime = "application/octet-stream"
    meta_path = path.with_suffix(".meta.txt")
    if meta_path.is_file():
        for line in meta_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("mime="):
                mime = line[5:].strip() or mime
    return {"bytes": data, "mime_type": mime, "row_key": row_key}


def _mime_to_data_uri_prefix(mime_type: str) -> str:
    mime = (mime_type or "image/jpeg").split(";")[0].strip().lower()
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    return f"data:{mime};base64,"


def _build_insert_data(origin_url: str, content: bytes, mime_type: str) -> str:
    """构造接口要求的 data 字段（JSON 字符串）。"""
    b64 = base64.b64encode(content).decode("ascii")
    payload = {
        "image_url": origin_url or "",
        "base64_data": _mime_to_data_uri_prefix(mime_type) + b64,
    }
    return json.dumps(payload, ensure_ascii=False)


def _write_http(row_key: str, payload: Dict[str, Any], cfg: Dict[str, Any]) -> None:
    """
    调用现成入库接口：
      POST insertUrl
      {"tableName": "...", "rowKey": "...", "data": "{image_url, base64_data}"}
    """
    url = cfg.get("insert_url") or ""
    if not url:
        raise HBaseStoreError("未配置 HERMES_HBASE_INSERT_URL")

    body = {
        "tableName": cfg["table"],
        "rowKey": row_key,
        "data": _build_insert_data(
            origin_url=str(payload.get("origin_url") or ""),
            content=payload["bytes"],
            mime_type=str(payload.get("mime_type") or "image/jpeg"),
        ),
    }
    timeout = max(5, int(cfg.get("timeout_ms") or 30000) / 1000.0)
    try:
        # 内网入库接口禁止走系统 HTTP_PROXY，否则易被本地代理打成 502
        resp = requests.post(
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
            proxies={"http": None, "https": None},
        )
    except Exception as exc:
        raise HBaseStoreError(f"HBase HTTP 写入请求失败: {exc}") from exc

    text = (resp.text or "").strip()
    if resp.status_code >= 400:
        raise HBaseStoreError(
            f"HBase HTTP 写入失败 HTTP {resp.status_code}: {text[:500]}"
        )
    # 接口返回形如 {"code":200,"message":"success",...}
    try:
        resp_obj = json.loads(text) if text else {}
    except Exception:
        resp_obj = {}
    if isinstance(resp_obj, dict) and resp_obj:
        code = resp_obj.get("code")
        if code is not None and int(code) != 200:
            raise HBaseStoreError(
                "HBase HTTP 写入业务失败 code={}: {}".format(code, text[:500])
            )
    logger.info(
        "HBase HTTP 写入成功 row_key=%s table=%s resp=%s",
        row_key,
        cfg["table"],
        text[:200],
    )


def exists(row_key: str) -> bool:
    """
    HTTP 写入接口无统一查询能力时，不做远端 exists。
    幂等依赖 run.py / MySQL storage_status；本地回退时检查文件。
    """
    cfg = hbase_config()
    if cfg["enabled"]:
        return False
    return _local_path(row_key, cfg["local_fallback_dir"]).is_file()


def put_image(
    task_id: str,
    sha256: str,
    content: bytes,
    mime_type: str,
    origin_url: str,
    file_size: int,
) -> str:
    """写入原图，返回 RowKey。"""
    row_key = build_row_key(task_id, sha256)
    if exists(row_key):
        logger.info("local image exists, skip write: %s", row_key)
        return row_key

    payload = {
        "bytes": content,
        "mime_type": mime_type,
        "origin_url": origin_url,
        "sha256": sha256,
        "file_size": file_size,
    }
    cfg = hbase_config()
    if cfg["enabled"]:
        _write_http(row_key, payload, cfg)
    else:
        logger.info("HBase 未启用，使用本地回退目录写入: %s", cfg["local_fallback_dir"])
        _write_local(row_key, payload, cfg["local_fallback_dir"])
    return row_key


def get_image(row_key: str) -> Optional[Dict[str, Any]]:
    """
    Python 侧读图仅支持本地回退（调试用）。
    生产读图由 Java HBaseImageClient（ZK 原生）完成。
    """
    cfg = hbase_config()
    if cfg["enabled"]:
        logger.warning("生产环境请通过 Java API 读原图；Python get_image 在 HTTP 模式下不可用")
        return None
    return _read_local(row_key, cfg["local_fallback_dir"])
