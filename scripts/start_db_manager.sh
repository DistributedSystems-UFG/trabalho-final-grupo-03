#!/usr/bin/env bash
# scripts/start_db_manager.sh
# Compila o Gerente de BD (se o JAR não existir) e o inicia.
#
# Uso:
#   bash scripts/start_db_manager.sh [--skip-build]

set -euo pipefail

SKIP_BUILD=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=true; shift ;;
    *) echo "Opção desconhecida: $1" >&2; exit 1 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs

JAR="db_manager/target/db-manager-1.0.0.jar"

if [ "$SKIP_BUILD" = false ]; then
  if [ ! -f "$JAR" ]; then
    echo "[db_manager] JAR não encontrado — compilando..."
    (cd db_manager && mvn package -q -DskipTests)
    echo "[db_manager] Build concluído."
  else
    echo "[db_manager] JAR existente encontrado, pulando build (use sem --skip-build para forçar rebuild)."
  fi
fi

if [ ! -f "$JAR" ]; then
  echo "[db_manager] ERRO: JAR não encontrado em $JAR" >&2
  exit 1
fi

echo "[db_manager] Iniciando Gerente de BD..."

exec java \
  -Xms256m -Xmx1g \
  -jar "$JAR"
