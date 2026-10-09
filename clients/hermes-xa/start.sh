#!/usr/bin/env bash
# Ubuntu：编译并前台启动 Java API（端口 4377）
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
PORT="${SERVER_PORT:-4377}"
if command -v ss >/dev/null 2>&1; then
  pid="$(ss -lntp | awk -v p=":$PORT" '$4 ~ p {print}' | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n 1 || true)"
  if [[ -n "${pid:-}" ]]; then
    echo "==> stop process on $PORT: $pid"
    kill "$pid" || true
    sleep 1
  fi
fi
mvn -DskipTests package
JAR="$(ls -1 target/*.jar | grep -v original | head -n 1)"
exec java -jar "$JAR"
