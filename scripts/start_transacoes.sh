#!/usr/bin/env bash
# scripts/start_transacoes.sh
# Inicia o Serviço de Transações (FastAPI + uvicorn).
#
# Uso:
#   bash scripts/start_transacoes.sh [--port 8003] [--workers 2]

set -euo pipefail

PORT=8003
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

echo "[transacoes] Iniciando na porta $PORT com $WORKERS worker(s)..."

exec uvicorn services.order.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "$WORKERS" \
  --log-level info
