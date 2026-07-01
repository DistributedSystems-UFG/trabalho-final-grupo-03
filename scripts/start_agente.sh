#!/usr/bin/env bash
# scripts/start_agente.sh
# Inicia o Agente de Manutenção (processo Python dedicado, loop contínuo).
# Sem porta — não expõe HTTP. Publica eventos no RabbitMQ e
# acessa dados via gRPC ao Gerente de BD.
#
# Uso:
#   bash scripts/start_agente.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs

echo "[agente] Iniciando agente de manutenção..."

exec python -m services.agente.worker
