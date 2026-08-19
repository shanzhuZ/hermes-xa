"""
HBase 原图写入 / 本地回退。

写入对齐现成 HTTP 接口（见「图片hbase入库.py」）：
  POST http://47.110.83.229:6666/insertHbaseData
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
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from image_pipeline.config import hbase_config

logger = logging.getLogger(__name__)

# 首次 HTTP 失败后短时跳过远端写入，避免视频每帧卡 30s（仅本进程）
_HTTP_FAIL_UNTIL = 0.0
_HTTP_FAIL_COOLDOWN_SEC = float(os.environ.get("HERMES_HBASE_FAIL_COOLDOWN_SEC", "300") or "300")


class HBaseStoreError(RuntimeError):
    pass


def _http_in_fail_cooldown() -> bool:
    return time.time() < float(_HTTP_FAIL_UNTIL)


def _trip_http_fail_cooldown(exc: Exception) -> None:
    global _HTTP_FAIL_UNTIL
    _HTTP_FAIL_UNTIL = time.time() + max(30.0, float(_HTTP_FAIL_COOLDOWN_SEC))
    logger.warning(
        "HBase HTTP 进入失败冷却 %.0fs，后续帧直接本地回退 err=%s",
        _HTTP_FAIL_COOLDOWN_SEC,
        exc,
    )


def build_row_key(task_id: str, sha256: str) -> str:
    """RowKey: img:{taskId}:{sha256前16}"""
    return f"img:{task_id}:{sha256[:16]}"


def build_frame_row_key(task_id: str, sha256: str) -> str:
    """视频抽帧 RowKey: frm:{taskId}:{sha256前16}（与 img: 同表、不冲突）"""
    return f"frm:{task_id}:{sha256[:16]}"


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
    timeout_ms = int(cfg.get("timeout_ms") or 8000)
    connect_s = max(1.0, float(cfg.get("connect_timeout_sec") or 3.0))
    read_s = max(connect_s, timeout_ms / 1000.0)
    try:
        # 内网入库接口禁止走系统 HTTP_PROXY，否则易被本地代理打成 502
        resp = requests.post(
            url,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=(connect_s, read_s),
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
    """写入原图，返回 RowKey。

    策略：
    1) 若启用 HBase：调 HTTP insert；再用 getHbaseData 校验；失败则记警告并依赖本地兜底
    2) **始终写本地回退目录**（Java 读 HBase 空时也可回退），避免「MySQL 已 stored 但两端都读不到」
    """
    return _put_bytes(
        build_row_key(task_id, sha256),
        content=content,
        mime_type=mime_type,
        origin_url=origin_url,
        sha256=sha256,
        file_size=file_size,
    )


def put_frame(
    task_id: str,
    sha256: str,
    content: bytes,
    mime_type: str = "image/jpeg",
    origin_url: str = "",
    file_size: int = 0,
) -> str:
    """写入视频抽帧图，返回 frm: RowKey（同表 collect_image_bytes）。"""
    return _put_bytes(
        build_frame_row_key(task_id, sha256),
        content=content,
        mime_type=mime_type or "image/jpeg",
        origin_url=origin_url or "",
        sha256=sha256,
        file_size=file_size or len(content),
    )


def _put_bytes(
    row_key: str,
    *,
    content: bytes,
    mime_type: str,
    origin_url: str,
    sha256: str,
    file_size: int,
) -> str:
    payload = {
        "bytes": content,
        "mime_type": mime_type,
        "origin_url": origin_url,
        "sha256": sha256,
        "file_size": file_size,
    }
    cfg = hbase_config()

    # 本地已有则直接复用（幂等）
    if _local_path(row_key, cfg["local_fallback_dir"]).is_file():
        logger.info("local image exists, skip write: %s", row_key)
        return row_key

    http_ok = False
    if cfg["enabled"] and not _http_in_fail_cooldown():
        try:
            _write_http(row_key, payload, cfg)
            if _verify_http(row_key, cfg):
                http_ok = True
            else:
                logger.warning(
                    "HBase HTTP 写入返回成功但 getHbaseData 读不到数据，将依赖本地回退 row_key=%s",
                    row_key,
                )
        except Exception as exc:
            _trip_http_fail_cooldown(exc)
            logger.warning("HBase HTTP 写入失败，改用本地回退 row_key=%s err=%s", row_key, exc)
    elif cfg["enabled"] and _http_in_fail_cooldown():
        logger.info("HBase HTTP 冷却中，跳过远端写入 row_key=%s", row_key)

    # 始终落本地，保证 Java / 历史详情能读到
    try:
        _write_local(row_key, payload, cfg["local_fallback_dir"])
        logger.info(
            "本地回退已写入 row_key=%s dir=%s http_ok=%s",
            row_key,
            cfg["local_fallback_dir"],
            http_ok,
        )
    except Exception as exc:
        if not http_ok:
            raise HBaseStoreError(f"HBase 与本地回退均写入失败: {exc}") from exc
        logger.warning("本地回退写入失败（HBase 已校验成功）row_key=%s err=%s", row_key, exc)
    return row_key


def _verify_http(row_key: str, cfg: Dict[str, Any]) -> bool:
    """用同机 getHbaseData 校验是否真正可读；接口异常或 data 为空视为未落库。"""
    insert_url = cfg.get("insert_url") or ""
    if not insert_url or "insertHbaseData" not in insert_url:
        return False
    get_url = insert_url.replace("insertHbaseData", "getHbaseData")
    timeout_ms = int(cfg.get("timeout_ms") or 8000)
    connect_s = max(1.0, float(cfg.get("connect_timeout_sec") or 3.0))
    read_s = max(connect_s, timeout_ms / 1000.0)
    try:
        resp = requests.post(
            get_url,
            json={"tableName": cfg["table"], "rowKey": row_key},
            headers={"Content-Type": "application/json"},
            timeout=(connect_s, read_s),
            proxies={"http": None, "https": None},
        )
        if resp.status_code >= 400:
            return False
        obj = resp.json() if resp.text else {}
        if not isinstance(obj, dict):
            return False
        data = obj.get("data")
        if data is None or data == "" or data == {}:
            return False
        # data 可能是 JSON 字符串或对象
        if isinstance(data, str) and "base64" not in data and len(data) < 8:
            return False
        return True
    except Exception as exc:
        logger.warning("getHbaseData 校验异常 row_key=%s err=%s", row_key, exc)
        return False


def get_image(row_key: str) -> Optional[Dict[str, Any]]:
    """
    Python 侧优先读本地回退；生产前端读图仍走 Java API。
    """
    cfg = hbase_config()
    local = _read_local(row_key, cfg["local_fallback_dir"])
    if local:
        return local
    if cfg["enabled"]:
        logger.warning("本地无文件且 Python 不直连 HBase ZK；请用 Java GET /api/images/{id}/bytes")
    return None