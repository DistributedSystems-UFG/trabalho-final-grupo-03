#!/usr/bin/env bash
# demo/scenario.sh
#
# Roteiro de demo para apresentação do projeto.
# Executa os cenários principais passo a passo, exibindo saídas formatadas.
#
# Uso:
#   bash demo/scenario.sh [gateway_url]
#   bash demo/scenario.sh http://api.scd-inventario.local

set -euo pipefail

GW="${1:-http://localhost:8080}"

# ── cores ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}▶ $*${RESET}"; }
ok()    { echo -e "${GREEN}  ✔ $*${RESET}"; }
info()  { echo -e "  $*"; }
warn()  { echo -e "${YELLOW}  ⚠ $*${RESET}"; }
fail()  { echo -e "${RED}  ✘ $*${RESET}"; }

hr() { echo -e "\n${BOLD}$(printf '─%.0s' {1..60})${RESET}"; }

# ── helper HTTP ───────────────────────────────────────────────────────────────
post() { curl -sf -X POST -H "Content-Type: application/json" "$@"; }
get()  { curl -sf -X GET  -H "Content-Type: application/json" "$@"; }
patch(){ curl -sf -X PATCH -H "Content-Type: application/json" "$@"; }

auth_header() { echo "-H X-Auth-Token:$1"; }

# ── 0. health check ───────────────────────────────────────────────────────────
hr
step "0. Health check"
HEALTH=$(get "$GW/health")
ok "Gateway respondeu: $HEALTH"

# ── 1. cadastro de usuários ───────────────────────────────────────────────────
hr
step "1. Cadastro de usuários"

SELLER=$(post "$GW/users" -d '{"username":"vendedor_demo","password":"senha123","role":"seller"}')
SELLER_TOKEN=$(echo "$SELLER" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
SELLER_ID=$(echo "$SELLER"    | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
ok "Vendedor criado  → id=${SELLER_ID:0:8}..."

BUYER=$(post "$GW/users" -d '{"username":"comprador_demo","password":"senha123","role":"buyer"}')
BUYER_TOKEN=$(echo "$BUYER" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
BUYER_ID=$(echo "$BUYER"    | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
ok "Comprador criado → id=${BUYER_ID:0:8}..."

# ── 2. cadastro de produto ────────────────────────────────────────────────────
hr
step "2. Vendedor cadastra produto (Eletrônicos → shard_a)"

PRODUCT=$(post "$GW/products" \
  -H "X-Auth-Token:$SELLER_TOKEN" \
  -d '{
    "name": "Notebook Gamer X1",
    "description": "16GB RAM, RTX 4060",
    "category": "Eletrônicos",
    "price": 4500.00,
    "quantity": 10,
    "alerta_quantidade": 3
  }')
PRODUCT_ID=$(echo "$PRODUCT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
ok "Produto criado → id=${PRODUCT_ID:0:8}..."

# ── 3. listagem de produtos ───────────────────────────────────────────────────
hr
step "3. Comprador lista produtos por categoria"

PRODUCTS=$(get "$GW/products?category=Eletr%C3%B4nicos" -H "X-Auth-Token:$BUYER_TOKEN")
COUNT=$(echo "$PRODUCTS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
ok "$COUNT produto(s) encontrado(s) em Eletrônicos"

# ── 4. compra direta ──────────────────────────────────────────────────────────
hr
step "4. Compra direta (REST síncrono)"

ORDER=$(post "$GW/orders" \
  -H "X-Auth-Token:$BUYER_TOKEN" \
  -d "{\"product_id\":\"$PRODUCT_ID\",\"quantity\":2}")
ORDER_ID=$(echo "$ORDER"    | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
TOTAL=$(echo "$ORDER"       | python3 -c "import sys,json; print(json.load(sys.stdin)['total_price'])")
STATUS=$(echo "$ORDER"      | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
ok "Pedido confirmado → id=${ORDER_ID:0:8}... total=R\$$TOTAL status=$STATUS"

# ── 5. watchlist ──────────────────────────────────────────────────────────────
hr
step "5. Comprador adiciona produto à watchlist"

WL=$(post "$GW/watchlist" \
  -H "X-Auth-Token:$BUYER_TOKEN" \
  -d "{\"product_id\":\"$PRODUCT_ID\",\"max_price\":5000.00}")
WL_ID=$(echo "$WL" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
ok "Watchlist criada → id=${WL_ID:0:8}... (alerta quando preço ≤ R\$5000)"

# ── 6. oferta relâmpago ───────────────────────────────────────────────────────
hr
step "6. Vendedor cria oferta relâmpago (20% de desconto, 2 minutos)"

FLASH=$(post "$GW/flash-offers" \
  -H "X-Auth-Token:$SELLER_TOKEN" \
  -d "{\"product_id\":\"$PRODUCT_ID\",\"discount_pct\":20,\"duration_minutes\":2}")
FLASH_ID=$(echo "$FLASH"     | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
PROMO=$(echo "$FLASH"        | python3 -c "import sys,json; print(json.load(sys.stdin)['promo_price'])")
EXPIRES=$(echo "$FLASH"      | python3 -c "import sys,json; print(json.load(sys.stdin)['expires_at'])")
ok "Oferta criada → id=${FLASH_ID:0:8}... promo_price=R\$$PROMO expira=$EXPIRES"

# ── 7. notificações (polling) ─────────────────────────────────────────────────
hr
step "7. Polling de notificações (aguarda 5s para o agente processar)"
sleep 5

NOTIFS_BUYER=$(get "$GW/notifications" -H "X-Auth-Token:$BUYER_TOKEN")
N_BUYER=$(echo "$NOTIFS_BUYER" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
ok "Comprador recebeu $N_BUYER notificação(ões)"

NOTIFS_SELLER=$(get "$GW/notifications" -H "X-Auth-Token:$SELLER_TOKEN")
N_SELLER=$(echo "$NOTIFS_SELLER" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
ok "Vendedor recebeu $N_SELLER notificação(ões)"

# marcar como lidas
if [ "$N_BUYER" -gt 0 ]; then
  IDS=$(echo "$NOTIFS_BUYER" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps([x['id'] for x in d]))")
  patch "$GW/notifications/read" -H "X-Auth-Token:$BUYER_TOKEN" -d "{\"ids\":$IDS}" > /dev/null
  ok "Notificações do comprador marcadas como lidas"
fi

# ── 8. status admin ───────────────────────────────────────────────────────────
hr
step "8. Status dos shards (admin)"

ADMIN_TOKEN="admin-secret-token"
STATUS_RESP=$(get "$GW/admin/status" -H "X-Auth-Token:$ADMIN_TOKEN" 2>/dev/null || echo '{"error":"sem acesso"}')
echo "$STATUS_RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'shards' in d:
    for s in d['shards']:
        failover = '⚠ FAILOVER ATIVO' if s.get('failover_active') else 'ok'
        print(f\"  [{s['shard_id']}] primary={s['primary_id'][-20:]} réplicas={len(s['replica_ids'])} status={failover}\")
else:
    print('  ' + str(d))
"

# ── 9. compra concorrente (stress) ────────────────────────────────────────────
hr
step "9. 5 compradores compram o mesmo produto simultaneamente"

EXTRA_BUYERS=()
for i in $(seq 1 4); do
  B=$(post "$GW/users" -d "{\"username\":\"buyer_stress_$i\",\"password\":\"x\",\"role\":\"buyer\"}")
  EXTRA_BUYERS+=("$(echo "$B" | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")")
done

# Dispara as 5 compras em paralelo (inclui o comprador original)
ALL_TOKENS=("$BUYER_TOKEN" "${EXTRA_BUYERS[@]}")
PIDS=()
for TOKEN in "${ALL_TOKENS[@]}"; do
  (
    RES=$(post "$GW/orders" -H "X-Auth-Token:$TOKEN" \
      -d "{\"product_id\":\"$PRODUCT_ID\",\"quantity\":1}" 2>&1 || echo '{"error":"conflict"}')
    ST=$(echo "$RES" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','conflict'))" 2>/dev/null || echo "conflict")
    echo "  comprador token=${TOKEN:0:8}... → $ST"
  ) &
  PIDS+=($!)
done
for pid in "${PIDS[@]}"; do wait "$pid"; done
ok "Compras concorrentes concluídas (conflitos de estoque são esperados e retornam 409)"

# ── fim ───────────────────────────────────────────────────────────────────────
hr
echo -e "\n${GREEN}${BOLD}Demo concluída com sucesso.${RESET}\n"
