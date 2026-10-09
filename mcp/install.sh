#!/usr/bin/env bash
# 将 mcp/mcp_servers.yaml 合并进仓库根目录 config.yaml
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$ROOT/scripts/sync_mcp_servers.py"
echo "[OK] mcp_servers merged into $ROOT/config.yaml"
