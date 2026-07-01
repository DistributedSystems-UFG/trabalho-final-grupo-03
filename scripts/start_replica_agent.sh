#!/usr/bin/env bash
# scripts/start_replica_agent.sh
# Compila o ReplicaAgent (se o JAR não existir) e inicia uma instância.
#
# A porta é DERIVADA automaticamente a partir de gerente_bd.qtd_max_replicas
# e gerente_bd.replicas_porta_base no config.yaml — não é necessário
# (nem possível) especificar uma porta manualmente.
#
# Uso:
#   bash scripts/start_replica_agent.sh --shard <shard_id> --index <N> [--skip-build]
#
#   shard_id: shard_a | shard_b | shard_c
#   N:        índice 0-based da réplica (0..qtd_max_replicas-1)
#
# Exemplos (com qtd_max_replicas: 2 → 6 instâncias):
#   bash scripts/start_replica_agent.sh --shard shard_a --index 0
#   bash scripts/start_replica_agent.sh --shard shard_a --index 1
#   bash scripts/start_replica_agent.sh --shard shard_b --index 0
#   bash scripts/start_replica_agent.sh --shard shard_b --index 1
#   bash scripts/start_replica_agent.sh --shard shard_c --index 0
#   bash scripts/start_replica_agent.sh --shard shard_c --index 1

set -euo pipefail

SHARD=""
INDEX=""
SKIP_BUILD=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --shard)      SHARD="$2";      shift 2 ;;
    --index)      INDEX="$2";      shift 2 ;;
    --skip-build) SKIP_BUILD=true; shift   ;;
    *) echo "Opção desconhecida: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$SHARD" ] || [ -z "$INDEX" ]; then
  echo "Uso: $0 --shard <shard_id> --index <N>" >&2
  echo "  shard_id: shard_a | shard_b | shard_c" >&2
  echo "  N:        índice 0-based da réplica (0..qtd_max_replicas-1)" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs

JAR="replica_agent/target/replica-agent-1.0.0.jar"

if [ "$SKIP_BUILD" = false ]; then
  if [ ! -f "$JAR" ]; then
    echo "[replica_agent] JAR não encontrado — compilando..."
    (cd replica_agent && mvn package -q -DskipTests)
    echo "[replica_agent] Build concluído."
  else
    echo "[replica_agent] JAR existente encontrado, pulando build."
  fi
fi

if [ ! -f "$JAR" ]; then
  echo "[replica_agent] ERRO: JAR não encontrado em $JAR" >&2
  exit 1
fi

echo "[replica_agent] Iniciando shard=$SHARD index=$INDEX (porta derivada automaticamente)..."

exec java \
  -Xms64m -Xmx256m \
  -jar "$JAR" \
  --shard "$SHARD" \
  --index "$INDEX"
