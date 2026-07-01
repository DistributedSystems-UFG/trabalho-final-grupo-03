#!/usr/bin/env bash
# scripts/start_everything.sh
# Sobe TODO o sistema com um único comando: replicas, db_manager,
# inventario, transacoes, notificacao, agente e gateway, em background,
# na ordem correta, com espera entre etapas. Não sobe RabbitMQ (assume
# que já está rodando via systemd/docker).
#
# Uso:
#   bash scripts/start_everything.sh [--port 8080]
#
# Logs em logs/*.log, PIDs em logs/all_pids.txt

echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
echo "Esse script é apenas para testes, não para produção"
echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"

set -euo pipefail

GATEWAY_PORT=8080
for ((i=1; i<=$#; i++)); do
  if [ "${!i}" = "--port" ]; then
    j=$((i+1))
    GATEWAY_PORT="${!j}"
  fi
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
mkdir -p logs

PIDS_FILE="logs/all_pids.txt"
> "$PIDS_FILE"

echo "[all] Verificando RabbitMQ..."
if ! (echo > /dev/tcp/localhost/5672) 2>/dev/null; then
  echo "[all] AVISO: RabbitMQ não parece estar rodando em localhost:5672."
  echo "[all]        Inicie com: sudo systemctl start rabbitmq-server"
fi

echo "[all] 1/6 Subindo ReplicaAgents..."
bash scripts/start_all_replicas.sh
sleep 3

echo "[all] 2/6 Subindo Gerente de BD..."
nohup bash scripts/start_db_manager.sh > logs/db_manager.log 2>&1 &
echo "$!" >> "$PIDS_FILE"
sleep 3

echo "[all] 3/6 Subindo Inventário e Transações..."
nohup bash scripts/start_inventario.sh --port 8002 > logs/inventario.log 2>&1 &
echo "$!" >> "$PIDS_FILE"
nohup bash scripts/start_transacoes.sh --port 8003 > logs/transacoes.log 2>&1 &
echo "$!" >> "$PIDS_FILE"
sleep 2

echo "[all] 4/6 Subindo Notificação..."
nohup bash scripts/start_notificacao.sh --port 8004 > logs/notificacao.log 2>&1 &
echo "$!" >> "$PIDS_FILE"
sleep 1

echo "[all] 5/6 Subindo Agente de Manutenção..."
nohup bash scripts/start_agente.sh > logs/agente.log 2>&1 &
echo "$!" >> "$PIDS_FILE"
sleep 1

echo "[all] 6/6 Subindo Gateway na porta $GATEWAY_PORT..."
nohup bash scripts/start_gateway.sh --port "$GATEWAY_PORT" > logs/gateway.log 2>&1 &
echo "$!" >> "$PIDS_FILE"
sleep 2

echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
echo "Esse script é apenas para testes, não para produção"
echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"

echo ""
echo "[all] Sistema no ar. Gateway: http://localhost:$GATEWAY_PORT"
echo "[all] Logs em logs/*.log — PIDs em $PIDS_FILE e logs/replica_pids.txt"
echo "[all] Para encerrar tudo: bash scripts/stop_everything.sh"
