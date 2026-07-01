#!/usr/bin/env bash
# scripts/start_inventario.sh
# Inicia o Serviço de Inventário (FastAPI + uvicorn).
#
# Uso:
#   bash scripts/start_inventario.sh [--port 8002] [--workers 2]

set -euo pipefail

PORT=8002
WORKERS=2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port)    PORT="$2";    shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    *) echo "Opção desconhecida: $1" >&2; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs

echo "[inventario] Iniciando na porta $PORT com $WORKERS worker(s)..."

exec uvicorn services.inventory.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "$WORKERS" \
  --log-level info
