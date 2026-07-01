#!/usr/bin/env bash
# scripts/start_all_replicas.sh
# Sobe TODOS os ReplicaAgents (3 shards × qtd_max_replicas), cada um em
# background, lendo qtd_max_replicas diretamente do config.yaml.
#
# Uso:
#   bash scripts/start_all_replicas.sh [--skip-build]
#
# Logs:
#   logs/replica_<shard>_<index>.log
#
# Para parar todas as instâncias:
#   bash scripts/stop_all_replicas.sh

set -euo pipefail

SKIP_BUILD=false
for arg in "$@"; do
  case "$arg" in
    --skip-build) SKIP_BUILD=true ;;
    *) echo "Opção desconhecida: $arg" >&2; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
mkdir -p logs

CONFIG_FILE="config.yaml"
TEMPLATE_FILE="config.template.yaml"

# Garante que config.yaml existe (mesmo bootstrap usado pelos componentes)
if [ ! -f "$CONFIG_FILE" ]; then
  echo "[replicas] config.yaml não encontrado — criando a partir do template..."
  cp "$TEMPLATE_FILE" "$CONFIG_FILE"
fi

# Lê qtd_max_replicas do config.yaml (default 2 se ausente)
QTD_REPLICAS=$(python3 - <<'PYEOF'
import yaml
with open("config.yaml") as f:
    cfg = yaml.safe_load(f) or {}
print(cfg.get("gerente_bd", {}).get("qtd_max_replicas", 2))
PYEOF
)

echo "[replicas] qtd_max_replicas=$QTD_REPLICAS — subindo $((QTD_REPLICAS * 3)) instância(s)..."

# Build único antecipado (evita 6 builds Maven concorrentes)
JAR="replica_agent/target/replica-agent-1.0.0.jar"
if [ "$SKIP_BUILD" = false ] && [ ! -f "$JAR" ]; then
  echo "[replicas] Compilando replica_agent..."
  (cd replica_agent && mvn package -q -DskipTests)
fi

PIDS_FILE="logs/replica_pids.txt"
> "$PIDS_FILE"

for SHARD in shard_a shard_b shard_c; do
  for ((INDEX=0; INDEX<QTD_REPLICAS; INDEX++)); do
    LOG="logs/replica_${SHARD}_${INDEX}.log"
    echo "[replicas] Iniciando ${SHARD}#${INDEX} (log: $LOG)..."
    nohup bash scripts/start_replica_agent.sh \
      --shard "$SHARD" --index "$INDEX" --skip-build \
      > "$LOG" 2>&1 &
    echo "$!" >> "$PIDS_FILE"
  done
done

echo "[replicas] Todas as instâncias iniciadas. PIDs salvos em $PIDS_FILE."
echo "[replicas] Aguarde alguns segundos antes de iniciar o Gerente de BD."
