#!/usr/bin/env bash
# scripts/stop_all_replicas.sh
# Para todas as instâncias de ReplicaAgent iniciadas por start_all_replicas.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PIDS_FILE="logs/replica_pids.txt"

if [ ! -f "$PIDS_FILE" ]; then
  echo "[replicas] Nenhum PID registrado em $PIDS_FILE."
  exit 0
fi

while read -r PID; do
  if kill -0 "$PID" 2>/dev/null; then
    echo "[replicas] Encerrando PID $PID..."
    kill "$PID"
  fi
done < "$PIDS_FILE"

rm -f "$PIDS_FILE"
echo "[replicas] Encerrado."
