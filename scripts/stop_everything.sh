#!/usr/bin/env bash
# scripts/stop_everything.sh
# Encerra tudo que start_everything.sh subiu, incluindo as réplicas.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ -f logs/all_pids.txt ]; then
  while read -r PID; do
    kill -0 "$PID" 2>/dev/null && kill "$PID" && echo "[all] Encerrado PID $PID"
  done < logs/all_pids.txt
  rm -f logs/all_pids.txt
fi

bash scripts/stop_all_replicas.sh

echo "[all] Sistema encerrado."
