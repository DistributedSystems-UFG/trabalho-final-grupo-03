#!/usr/bin/env bash
# scripts/start_notificacao.sh
# Inicia o Serviço de Notificação (FastAPI + uvicorn).
# O consumer RabbitMQ sobe em thread separada dentro do mesmo processo.
#
# Uso:
#   bash scripts/start_notificacao.sh [--port 8004] [--workers 1]

set -euo pipefail

PORT=8004
WORKERS=1   # 1 worker para garantir que o consumer RabbitMQ rode em thread única

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

echo "[notificacao] Iniciando na porta $PORT com $WORKERS worker(s)..."

exec uvicorn services.notification.main:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --workers "$WORKERS" \
  --log-level info
