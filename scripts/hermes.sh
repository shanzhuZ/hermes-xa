#!/usr/bin/env bash
# 在仓库根目录运行 hermes CLI / gateway，确保读取本仓库 config.yaml
# 用法：./scripts/hermes.sh gateway
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HERMES_HOME="$ROOT"
export PYTHONPATH="${ROOT}/scripts${PYTHONPATH:+:$PYTHONPATH}"
if [[ -x "${ROOT}/.venv/bin/hermes" ]]; then
  exec "${ROOT}/.venv/bin/hermes" "$@"
fi
exec hermes "$@"
