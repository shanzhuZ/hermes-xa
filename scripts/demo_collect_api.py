#!/usr/bin/env python3
"""已废弃：请改用 Spring Boot clients/hermes-xa（CollectApiController，默认端口 4377）。

保留本文件仅供本地对照；不再维护。
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib import error, request

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from collect_01.config import load_env
from collect_01.task_store import TaskStore
from collect_01.task_tree import build_task_tree, get_node_detail

load_env()

GATEWAY_BASE = os.environ.get("HERMES_GATEWAY_URL", "http://127.0.0.1:8642").rstrip("/")
GATEWAY_KEY = os.environ.get(
    "HERMES_GATEWAY_API_KEY",
    "dc9b5db558aa4844d0a29d79deb296b75aba58a670c79ecff3ed922839b2c86b",
)
API_PORT = int(os.environ.get("HERMES_DEMO_API_PORT", "8088"))


def _json_response(handler: BaseHTTPRequestHandler, code: int, body: Dict[str, Any]) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_json(handler: BaseHTTPRequestHandler) -> Dict[str, Any]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _gateway_json(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, Any]]:
    url = f"{GATEWAY_BASE}{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {GATEWAY_KEY}",
        "Content-Type": "application/json; charset=utf-8",
    }
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8")
            return resp.status, json.loads(text) if text else {}
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(text) if text else {"error": text}
        except json.JSONDecodeError:
            payload = {"error": text}
        return exc.code, payload


def _parse_session_id(resp: Dict[str, Any]) -> Optional[str]:
    session = resp.get("session")
    if isinstance(session, dict):
        for key in ("id", "session_id"):
            val = session.get(key)
            if val:
                return str(val)
    for key in ("id", "session_id"):
        val = resp.get(key)
        if val:
            return str(val)
    text = json.dumps(resp, ensure_ascii=False)
    m = re.search(r'"(?:id|session_id)"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _drain_gateway_stream(session_id: str, task_id: str, user_message: str) -> None:
    body = json.dumps({"input": user_message}, ensure_ascii=False).encode("utf-8")
    url = f"{GATEWAY_BASE}/api/sessions/{session_id}/chat/stream"
    req = request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {GATEWAY_KEY}")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Accept", "text/event-stream")
    req.add_header("X-Hermes-Session-Key", f"task:{task_id}")
    try:
        with request.urlopen(req, timeout=3600) as resp:
            event_name = ""
            while True:
                line = resp.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if text.startswith("event:"):
                    event_name = text[6:].strip()
                elif text.startswith("data:") and event_name:
                    # 简易日志，便于排查
                    print(f"[gateway] {event_name}: {text[5:].strip()[:200]}", flush=True)
                    if event_name == "done":
                        break
    except Exception as exc:
        print(f"[gateway] stream error task={task_id}: {exc}", flush=True)
        TaskStore().mark_task_failed(task_id, str(exc))


class DemoHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[demo-api] {self.address_string()} - {fmt % args}", flush=True)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        m_tree = re.match(r"^/api/tasks/([^/]+)/tree$", path)
        if m_tree:
            task_id = m_tree.group(1)
            return _json_response(self, 200, build_task_tree(task_id))

        m_node = re.match(r"^/api/tasks/([^/]+)/nodes/([^/]+)$", path)
        if m_node:
            return _json_response(self, 200, get_node_detail(m_node.group(1), m_node.group(2)))

        m_task = re.match(r"^/api/tasks/([^/]+)$", path)
        if m_task:
            task = TaskStore().get_task(m_task.group(1))
            if not task:
                return _json_response(self, 404, {"error": "task_not_found"})
            return _json_response(
                self,
                200,
                {
                    "taskId": task.get("task_id"),
                    "sessionId": task.get("session_id"),
                    "status": task.get("status"),
                    "currentPhase": task.get("current_phase"),
                    "crossPlatform": task.get("cross_platform"),
                    "startedAt": str(task.get("started_at") or ""),
                    "finishedAt": str(task.get("finished_at") or ""),
                    "treeUrl": f"/api/tasks/{task.get('task_id')}/tree",
                },
            )

        m_conv = re.match(r"^/api/conversations/([^/]+)/tasks$", path)
        if m_conv:
            session_id = m_conv.group(1)
            rows = TaskStore().list_tasks_by_session(session_id)
            return _json_response(
                self,
                200,
                {
                    "sessionId": session_id,
                    "tasks": [
                        {
                            "taskId": r.get("task_id"),
                            "status": r.get("status"),
                            "createdAt": str(r.get("created_at") or ""),
                            "treeUrl": f"/api/tasks/{r.get('task_id')}/tree",
                        }
                        for r in rows
                    ],
                },
            )

        return _json_response(self, 404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/api/conversations":
            code, resp = _gateway_json("POST", "/api/sessions", {})
            if code < 200 or code >= 300:
                return _json_response(self, code, {"error": "gateway_create_session_failed", "detail": resp})
            session_id = _parse_session_id(resp)
            if not session_id:
                return _json_response(self, 502, {"error": "cannot_parse_session_id", "detail": resp})
            return _json_response(self, 201, {"sessionId": session_id, "gateway": resp})

        if path == "/api/collect":
            body = _read_json(self)
            session_id = str(body.get("sessionId") or body.get("session_id") or "").strip()
            message = str(body.get("message") or body.get("input") or "").strip()
            task_id = str(body.get("taskId") or body.get("task_id") or "").strip() or str(uuid.uuid4())
            if not session_id:
                return _json_response(self, 400, {"error": "sessionId_required"})
            if not message:
                return _json_response(self, 400, {"error": "message_required"})
            store = TaskStore()
            try:
                store.create_pending_task(task_id, session_id, message)
            except ValueError as exc:
                return _json_response(self, 409, {"error": str(exc)})
            threading.Thread(
                target=_drain_gateway_stream,
                args=(session_id, task_id, message),
                daemon=True,
            ).start()
            return _json_response(
                self,
                202,
                {
                    "taskId": task_id,
                    "sessionId": session_id,
                    "status": "pending",
                    "pollTreeUrl": f"/api/tasks/{task_id}/tree",
                    "pollTaskUrl": f"/api/tasks/{task_id}",
                },
            )

        return _json_response(self, 404, {"error": "not_found", "path": path})


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", API_PORT), DemoHandler)
    print(f"demo_collect_api listening on http://127.0.0.1:{API_PORT}", flush=True)
    print("Postman 流程:", flush=True)
    print("  1. POST /api/conversations", flush=True)
    print("  2. POST /api/collect  {sessionId, message}", flush=True)
    print("  3. GET  /api/tasks/{taskId}/tree  (轮询)", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
