# Documentação de Implementação — Sistema de Controle de Inventário

**Disciplina:** Software Concorrente e Distribuído — UFG/INF 2026.1  
**Professor:** Fábio Moreira Costa  
**Entrega:** 02/07/2026

## 1. Endpoints REST

Todos os endpoints são acessados através do Gateway (`http://api.scd-inventario.local`). O header `X-Auth-Token: <token>` é obrigatório em todas as requisições exceto `POST /users` e `GET /health`.

### Usuários

| Método | Rota | Role permitido | Body | Resposta |
| --- | --- | --- | --- | --- |
| `POST` | `/auth/register` | qualquer (sem auth) | `{ "username": str, "password": str, "role": "buyer"\|"seller" }` | `{ "id": str, "token": str }` |
| `POST` | `/auth/login` | qualquer (sem auth) | `{ "username": str, "password": str }` | `{ "id": str, "token": str }` |
| `GET` | `/health` | qualquer (sem auth) | — | `{ "status": "ok" }` |

> A senha é armazenada como `SHA-256(password)`. O token é gerado no registro e no login como `SHA-256(username + password + timestamp)` e sobrescrito a cada novo login. O Gateway resolve `token → user_id` consultando o Gerente de BD a cada requisição.

### Produtos

| Método | Rota | Role permitido | Body / Params | Resposta |
| --- | --- | --- | --- | --- |
| `GET` | `/products` | buyer, seller | query: `?category=&name=` | `[{ "id", "name", "category", "price", "quantity", "seller_id" }]` |
| `GET` | `/products/{id}` | buyer, seller | — | `{ "id", "name", "category", "price", "quantity", "seller_id", "alerta_quantidade" }` |
| `POST` | `/products` | seller | `{ "name": str, "description": str, "category": str, "price": float, "quantity": int, "alerta_quantidade": int }` | `{ "id": str }` |
| `PUT` | `/products/{id}` | seller (dono) | `{ "price": float, "quantity": int, "alerta_quantidade": int }` | `{ "ok": true }` |
| `DELETE` | `/products/{id}` | seller (dono) | — | `{ "ok": true }` |

### Pedidos (compra direta)

| Método | Rota | Role permitido | Body | Resposta |
| --- | --- | --- | --- | --- |
| `POST` | `/orders` | buyer | `{ "product_id": str, "quantity": int }` | `{ "id": str, "total_price": float, "status": "confirmed" }` |
| `GET` | `/orders` | buyer, seller | — | `[{ "id", "product_id", "quantity", "total_price", "status", "created_at" }]` |

> O filtro de `GET /orders` é aplicado pelo Serviço de transações com base no `user_id` resolvido pelo Gateway: buyer recebe pedidos onde `buyer_id = user_id`; seller recebe pedidos onde `seller_id = user_id`.

### Watchlist

| Método | Rota | Role permitido | Body | Resposta |
| --- | --- | --- | --- | --- |
| `POST` | `/watchlist` | buyer | `{ "product_id": str, "max_price": float }` | `{ "id": str }` |
| `GET` | `/watchlist` | buyer | — | `[{ "id", "product_id", "max_price", "notified" }]` |
| `DELETE` | `/watchlist/{id}` | buyer (dono) | — | `{ "ok": true }` |

### Ofertas relâmpago

| Método | Rota | Role permitido | Body | Resposta |
| --- | --- | --- | --- | --- |
| `POST` | `/flash-offers` | seller | `{ "product_id": str, "discount_pct": float, "duration_minutes": int }` | `{ "id": str, "promo_price": float, "expires_at": str }` |
| `GET` | `/flash-offers` | buyer, seller | query: `?category=` | `[{ "id", "product_id", "promo_price", "expires_at" }]` |

### Notificações

| Método | Rota | Role permitido | Body | Resposta |
| --- | --- | --- | --- | --- |
| `GET` | `/notifications` | buyer, seller | — | `[{ "id", "message", "created_at" }]` |
| `PATCH` | `/notifications/read` | buyer, seller | `{ "ids": [str] }` | `{ "ok": true }` |

### Admin

| Método | Rota | Role permitido | Body | Resposta |
| --- | --- | --- | --- | --- |
| `GET` | `/admin/db-status` | admin | — | `{ "shards": [{ "shard_id", "primary_id", "replica_ids", "failover_active" }] }` |
| `POST` | `/admin/promote-replica` | admin | `{ "shard_id": str, "replica_id": str }` | `{ "ok": true, "new_primary": str }` |

## 2. Payloads dos eventos RabbitMQ

Todos os eventos são publicados como JSON em exchanges do tipo `topic`. O Serviço de notificação é o único consumidor.

### `stock.low`

Publicado pelo Agente de manutenção quando estoque cai abaixo do limiar.

```json
{
  "event": "stock.low",
  "product_id": "uuid",
  "product_name": "string",
  "seller_id": "uuid",
  "quantity": 3,
  "alerta_quantidade": 5
}
```

### `order.completed`

Publicado pelo Serviço de transações após confirmação de compra.

```json
{
  "event": "order.completed",
  "order_id": "uuid",
  "product_id": "uuid",
  "product_name": "string",
  "buyer_id": "uuid",
  "seller_id": "uuid",
  "quantity": 2,
  "total_price": 199.90
}
```

### `price.alert`

Publicado pelo Agente de manutenção quando preço de produto atinge o alvo de uma watchlist.

```json
{
  "event": "price.alert",
  "watchlist_id": "uuid",
  "product_id": "uuid",
  "product_name": "string",
  "buyer_id": "uuid",
  "current_price": 89.90,
  "max_price": 100.00
}
```

### `flash.offer`

Publicado pelo Serviço de inventário quando vendedor cria oferta relâmpago.

```json
{
  "event": "flash.offer",
  "flash_offer_id": "uuid",
  "product_id": "uuid",
  "product_name": "string",
  "category": "string",
  "seller_id": "uuid",
  "original_price": 150.00,
  "promo_price": 105.00,
  "expires_at": "2026-06-24T15:00:00Z"
}
```

## 3. Schema do banco de dados

Schema definido e mantido pelo Gerente de BD, idêntico em todos os arquivos `.db`:

```sql
-- Usuários [GLOBAL — replicada nos 3 shards]
CREATE TABLE users (
  id            TEXT PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,  -- SHA-256 da senha recebida no registro
  role          TEXT NOT NULL CHECK(role IN ('buyer','seller','admin')),
  token         TEXT NOT NULL   -- SHA-256(username + password + timestamp), sobrescrito a cada login
);

-- Produtos [PARTICIONADA por category]
CREATE TABLE products (
  id                TEXT PRIMARY KEY,
  seller_id         TEXT NOT NULL REFERENCES users(id),
  name              TEXT NOT NULL,
  description       TEXT,
  category          TEXT NOT NULL,
  price             REAL NOT NULL,
  quantity          INTEGER NOT NULL CHECK(quantity >= 0),
  alerta_quantidade INTEGER NOT NULL DEFAULT 5,
  alerta_enviado    INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  expires_at        TEXT
);

-- Pedidos [PARTICIONADA pelo shard do produto]
CREATE TABLE orders (
  id          TEXT PRIMARY KEY,
  buyer_id    TEXT NOT NULL REFERENCES users(id),
  seller_id   TEXT NOT NULL REFERENCES users(id),
  product_id  TEXT NOT NULL REFERENCES products(id),
  quantity    INTEGER NOT NULL,
  total_price REAL NOT NULL,
  status      TEXT NOT NULL CHECK(status IN ('pending','confirmed','cancelled')),
  created_at  TEXT NOT NULL
);

-- Watchlist de preço [PARTICIONADA pelo shard do produto]
CREATE TABLE watchlist (
  id         TEXT PRIMARY KEY,
  buyer_id   TEXT NOT NULL REFERENCES users(id),
  product_id TEXT NOT NULL REFERENCES products(id),
  max_price  REAL NOT NULL,
  notified   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

-- Ofertas relâmpago [PARTICIONADA pelo shard do produto]
CREATE TABLE flash_offers (
  id             TEXT PRIMARY KEY,
  product_id     TEXT NOT NULL REFERENCES products(id),
  original_price REAL NOT NULL,
  promo_price    REAL NOT NULL,
  status         TEXT NOT NULL CHECK(status IN ('active','expired')),
  created_at     TEXT NOT NULL,
  expires_at     TEXT NOT NULL
);

-- Notificações [GLOBAL — replicada nos 3 shards]
CREATE TABLE notifications (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id),
  message    TEXT NOT NULL,
  read       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
```

## 4. Controle de concorrência — código de referência

```java
// Gerente de BD — Java
private final Map<String, ConcurrentHashMap<String, ReentrantLock>> shardProductLocks =
    new HashMap<>(); // shard_id → (product_id → lock)
private final Map<String, ReentrantLock> shardGlobalLocks = new HashMap<>(); // shard_id → lock global

public WriteAck applyWrite(WriteRequest req) {
    if (req.getCategory().equals("global")) {
        return applyGlobalWrite(req); // aplica nos 3 principais em paralelo
    }
    String shardId = shardRouter.route(req.getCategory());
    ReentrantLock lock = req.getProductId().isEmpty()
        ? shardGlobalLocks.get(shardId)
        : shardProductLocks.get(shardId)
              .computeIfAbsent(req.getProductId(), k -> new ReentrantLock());
    lock.lock();
    try {
        if (isDuplicate(req.getOriginId()))
            return WriteAck.newBuilder().setSuccess(true).build(); // idempotência
        applyToPrimary(shardId, req.getSql(), req.getParamsList());
        replicateAsync(shardId, req); // gRPC ApplyWrite para cada ReplicaAgent remoto
        markProcessed(req.getOriginId());
        return WriteAck.newBuilder().setSuccess(true).build();
    } catch (Exception e) {
        return WriteAck.newBuilder().setSuccess(false).setError(e.getMessage()).build();
    } finally {
        lock.unlock();
    }
}
```

## 5. Clientes CLI — Módulos HTTP

Os módulos em `clients/http/` usam apenas stdlib do Python (`urllib.request`, `urllib.error`, `json`, `os`) — sem dependências externas. Cada módulo pode ser executado diretamente com `python <módulo>.py` como smoke test.

### 5.1 Módulo auxiliar compartilhado — `clients/http/_http.py`

Centraliza a montagem de cabeçalhos, serialização JSON e tratamento de erros. Lê `GATEWAY_URL` (padrão: `http://api.scd-inventario.local:8080`) e `GATEWAY_TOKEN` de variáveis de ambiente.

```python
# clients/http/_http.py
import json
import os
import urllib.request
import urllib.error

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://api.scd-inventario.local:8080")

def _request(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    """Envia uma requisição HTTP ao Gateway e retorna o corpo como dict.

    Retorno:
        dict com o JSON da resposta em caso de sucesso.
        {"error": "<msg>", "status": <código>} em caso de erro HTTP.
        {"error": "<msg>", "status": 0}        em caso de falha de rede.
    """
    url = GATEWAY_URL + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    tok = token or os.getenv("GATEWAY_TOKEN", "")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except Exception:
            detail = e.reason
        return {"error": detail, "status": e.code}
    except urllib.error.URLError as e:
        return {"error": str(e.reason), "status": 0}
```

### 5.2 `clients/http/auth_http.py`

```python
# clients/http/auth_http.py
"""Módulo de autenticação — zero dependências externas.
Uso: python auth_http.py
"""
from _http import _request

def register(username: str, password: str, role: str) -> dict:
    """POST /auth/register → {"id": ..., "token": ...}"""
    return _request("POST", "/auth/register",
                    {"username": username, "password": password, "role": role})

def login(username: str, password: str) -> dict:
    """POST /auth/login → {"id": ..., "token": ...}"""
    return _request("POST", "/auth/login",
                    {"username": username, "password": password})

if __name__ == "__main__":
    print("=== register ===")
    print(register("alice", "s3cr3t", "buyer"))
    print("=== login ===")
    print(login("alice", "s3cr3t"))
```

### 5.3 `clients/http/inventory_http.py`

```python
# clients/http/inventory_http.py
"""Módulo de inventário — zero dependências externas.
Uso: python inventory_http.py
"""
from _http import _request

# --- Produtos ---

def list_products(token: str, category: str | None = None, name: str | None = None) -> dict:
    """GET /products[?category=&name=] → [{"id", "name", "category", "price", "quantity", "seller_id"}]"""
    params = "&".join(f"{k}={v}" for k, v in [("category", category), ("name", name)] if v)
    path = "/products" + (f"?{params}" if params else "")
    return _request("GET", path, token=token)

def get_product(token: str, product_id: str) -> dict:
    """GET /products/{id} → {"id", "name", "category", "price", "quantity", "seller_id", "alerta_quantidade"}"""
    return _request("GET", f"/products/{product_id}", token=token)

def create_product(token: str, name: str, description: str, category: str,
                   price: float, quantity: int, alerta_quantidade: int) -> dict:
    """POST /products → {"id": str}"""
    body = {"name": name, "description": description, "category": category,
            "price": price, "quantity": quantity, "alerta_quantidade": alerta_quantidade}
    return _request("POST", "/products", body, token=token)

def update_product(token: str, product_id: str, **fields) -> dict:
    """PUT /products/{id} → {"ok": true}"""
    return _request("PUT", f"/products/{product_id}", fields, token=token)

def delete_product(token: str, product_id: str) -> dict:
    """DELETE /products/{id} → {"ok": true}"""
    return _request("DELETE", f"/products/{product_id}", token=token)

# --- Watchlist ---

def add_watchlist(token: str, product_id: str, max_price: float) -> dict:
    """POST /watchlist → {"id": str}"""
    return _request("POST", "/watchlist",
                    {"product_id": product_id, "max_price": max_price}, token=token)

def list_watchlist(token: str) -> dict:
    """GET /watchlist → [{"id", "product_id", "max_price", "notified"}]"""
    return _request("GET", "/watchlist", token=token)

def remove_watchlist(token: str, watchlist_id: str) -> dict:
    """DELETE /watchlist/{id} → {"ok": true}"""
    return _request("DELETE", f"/watchlist/{watchlist_id}", token=token)

# --- Ofertas relâmpago ---

def create_flash_offer(token: str, product_id: str,
                       discount_pct: float, duration_minutes: int) -> dict:
    """POST /flash-offers → {"id": str, "promo_price": float, "expires_at": str}"""
    body = {"product_id": product_id,
            "discount_pct": discount_pct, "duration_minutes": duration_minutes}
    return _request("POST", "/flash-offers", body, token=token)

def list_flash_offers(token: str, category: str | None = None) -> dict:
    """GET /flash-offers[?category=] → [{"id", "product_id", "promo_price", "expires_at"}]"""
    path = "/flash-offers" + (f"?category={category}" if category else "")
    return _request("GET", path, token=token)

if __name__ == "__main__":
    import os
    tok = os.getenv("GATEWAY_TOKEN", "TOKEN_EXEMPLO")
    print("=== list_products ===")
    print(list_products(tok))
    print("=== list_flash_offers ===")
    print(list_flash_offers(tok))
```

### 5.4 `clients/http/transaction_http.py`

```python
# clients/http/transaction_http.py
"""Módulo de transações — zero dependências externas.
Uso: python transaction_http.py
"""
from _http import _request

def buy(token: str, product_id: str, quantity: int) -> dict:
    """POST /orders → {"id": str, "total_price": float, "status": "confirmed"} | 409"""
    return _request("POST", "/orders",
                    {"product_id": product_id, "quantity": quantity}, token=token)

def list_orders(token: str) -> dict:
    """GET /orders → [{"id", "product_id", "quantity", "total_price", "status", "created_at"}]"""
    return _request("GET", "/orders", token=token)

if __name__ == "__main__":
    import os
    tok = os.getenv("GATEWAY_TOKEN", "TOKEN_EXEMPLO")
    print("=== list_orders ===")
    print(list_orders(tok))
    print("=== buy (produto exemplo, qtd 1) ===")
    print(buy(tok, product_id="produto-uuid-aqui", quantity=1))
```

### 5.5 `clients/http/notification_http.py`

```python
# clients/http/notification_http.py
"""Módulo de notificações — zero dependências externas.
Uso: python notification_http.py
"""
from _http import _request

def get_notifications(token: str) -> dict:
    """GET /notifications → [{"id", "message", "created_at"}]"""
    return _request("GET", "/notifications", token=token)

def mark_read(token: str, ids: list[str]) -> dict:
    """PATCH /notifications/read → {"ok": true}"""
    return _request("PATCH", "/notifications/read", {"ids": ids}, token=token)

if __name__ == "__main__":
    import os
    tok = os.getenv("GATEWAY_TOKEN", "TOKEN_EXEMPLO")
    print("=== get_notifications ===")
    result = get_notifications(tok)
    print(result)
    notif_ids = [n["id"] for n in (result if isinstance(result, list) else [])]
    if notif_ids:
        print("=== mark_read ===")
        print(mark_read(tok, notif_ids))
```

### 5.6 `clients/http/admin_http.py`

```python
# clients/http/admin_http.py
"""Módulo de administração — zero dependências externas.
Uso: python admin_http.py
"""
from _http import _request

def get_health() -> dict:
    """GET /health → {"status": "ok"}"""
    return _request("GET", "/health")

def get_db_status(token: str) -> dict:
    """GET /admin/db-status → {"shards": [{"shard_id", "primary_id", "replica_ids", "failover_active"}]}"""
    return _request("GET", "/admin/db-status", token=token)

def promote_replica(token: str, shard_id: str, replica_id: str) -> dict:
    """POST /admin/promote-replica → {"ok": true, "new_primary": str}"""
    return _request("POST", "/admin/promote-replica",
                    {"shard_id": shard_id, "replica_id": replica_id}, token=token)

if __name__ == "__main__":
    import os
    tok = os.getenv("GATEWAY_TOKEN", "TOKEN_ADMIN")
    print("=== health ===")
    print(get_health())
    print("=== db_status ===")
    print(get_db_status(tok))
```

### 5.7 CLIs — estrutura comum

Os CLIs em `clients/` importam os módulos HTTP via `sys.path.insert` e não fazem HTTP diretamente. Cada CLI:

- Aceita `--gateway-url` e `--poll-interval` via `argparse`; repassa a URL para os módulos HTTP via `os.environ["GATEWAY_URL"]`.
- Mantém o token em memória após o login (nunca em disco).
- Exibe um menu numerado em loop até o usuário sair.
- Roda o polling de notificações em `threading.Thread` com `daemon=True`, chamando `notification_http.get_notifications` a cada `--poll-interval` segundos e `mark_read` para as notificações exibidas.

```python
# Trecho comum a todos os CLIs
import argparse, os, sys, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "http"))
import auth_http, notification_http  # + módulos específicos do papel

def poll_notifications(token: str, interval: int, stop: threading.Event):
    while not stop.is_set():
        result = notification_http.get_notifications(token)
        notifs = result if isinstance(result, list) else []
        if notifs:
            print(f"\n[NOTIFICAÇÃO] {len(notifs)} nova(s):")
            for n in notifs:
                print(f"  • {n['message']}")
            notification_http.mark_read(token, [n["id"] for n in notifs])
        stop.wait(interval)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-url", default="http://localhost:8080")
    parser.add_argument("--poll-interval", type=int, default=5)
    args = parser.parse_args()
    os.environ["GATEWAY_URL"] = args.gateway_url
    # ... login, start polling thread, menu loop
```

## 6. Protobufs (gRPC)

### 6.1 Interface pública — Gerente de BD (usada pelos serviços Python)

```protobuf
syntax = "proto3";
package dbmanager;

service DBManager {
  rpc Read            (ReadRequest)    returns (ReadResult);
  rpc Write           (WriteRequest)   returns (WriteAck);
  rpc HealthCheck     (HealthRequest)  returns (HealthResponse);
  rpc GetStatus       (StatusRequest)  returns (StatusResponse);
  rpc PromoteReplica  (PromoteRequest) returns (PromoteAck);
}

// Leitura — category guia o ShardRouter; vazia = executa nos 3 principais e mescla
message ReadRequest {
  string category        = 1;
  string sql             = 2;
  repeated string params = 3;
}
message ReadResult {
  bool   success         = 1;
  string error           = 2;
  repeated string rows   = 3;
}

// Escrita — idempotente via origin_id; category guia o ShardRouter
message WriteRequest {
  string category        = 1;
  string sql             = 2;
  repeated string params = 3;
  string origin_id       = 4;
  string product_id      = 5;
}
message WriteAck {
  bool   success         = 1;
  string error           = 2;
}

message HealthRequest {}
message HealthResponse {
  bool   healthy         = 1;
  int32  replicas_online = 2;
  int32  replicas_total  = 3;
}

message StatusRequest {}
message ShardStatus {
  string shard_id              = 1;
  string primary_id            = 2;
  repeated string replica_ids  = 3;
  bool   failover_active       = 4;
}
message StatusResponse {
  repeated ShardStatus shards  = 1;
}

message PromoteRequest {
  string shard_id   = 1;
  string replica_id = 2;
}
message PromoteAck {
  bool   success     = 1;
  string new_primary = 2;
}
```

### 6.2 Interface interna — ReplicaAgent (usada apenas pelo Gerente de BD)

```protobuf
syntax = "proto3";
package replica;

service ReplicaAgent {
  rpc ApplyWrite (WriteRequest) returns (WriteAck);
  rpc Ping       (PingRequest)  returns (PingResponse);
}

// Reutiliza a mesma estrutura de WriteRequest/WriteAck do dbmanager.proto
message WriteRequest {
  string sql             = 1;
  repeated string params = 2;
  string origin_id       = 3;
}
message WriteAck {
  bool   success         = 1;
  string error           = 2;
}

message PingRequest {}
message PingResponse {
  bool   ok              = 1;
  string db_path         = 2; // caminho do arquivo .db local, para diagnóstico
}
```

## 7. Configuração

### 7.1 Bootstrap automático na primeira execução

Nenhum componente assume que o `config.yaml` existe. Cada processo executa um bootstrap de configuração antes de qualquer outra inicialização: verifica se `config.yaml` existe no diretório de trabalho e, caso contrário, copia o `config.template.yaml` embutido.

```markdown
ao iniciar qualquer componente:
  → verifica se ./config.yaml existe
  → se não existe:
      → copia config.template.yaml → config.yaml
      → loga: "config.yaml não encontrado — criado a partir do template padrão"
  → carrega config.yaml e prossegue
```

> `config.yaml` deve estar no `.gitignore`. `config.template.yaml` deve estar versionado.

**Python** → bootstrap feito por `shared/config.py`, importado como primeiro passo de cada serviço.  
**Java** → bootstrap feito por `ConfigLoader.java`, primeira instrução do `main()`. Template em `db_manager/src/main/resources/config.template.yaml` — cópia do template da raiz; manter sincronizado (instrução no README).

### 7.2 Conteúdo do config.template.yaml

```yaml
servidor:
  host: "0.0.0.0"
  porta: 8080

# Endereços dos serviços internos — usados pelo Gateway para roteamento
servicos:
  inventario:
    host: "localhost"
    porta: 8002
  transacoes:
    host: "localhost"
    porta: 8003
  notificacao:
    host: "localhost"
    porta: 8004

gerente_bd:
  host: "localhost"
  porta: 50050
  qtd_max_replicas: 2
  replicas_host: "localhost"
  replicas_porta_base: 50100

fila_mensagens:
  host: "localhost"
  porta: 5672
  usuario: "guest"
  senha: "guest"

agente_manutencao:
  intervalo_segundos: 30

dados:
  diretorio: "./data"

auth:
  token_admin: "admin-secret-token"  # ALTERAR antes de deploy em produção
```

No EC2, substituir todos os `localhost` pelos IPs privados das instâncias correspondentes.

## 8. Implantação no AWS EC2

### 8.1 Decisões de distribuição

O sistema é composto por sete grupos funcionais distribuídos em 6 instâncias EC2, seguindo dois critérios: **isolamento de responsabilidade** e **afinidade de comunicação**.

**Gateway A (EC2-1a) e Gateway B (EC2-1b)**
Cada Gateway fica em uma instância separada — essa separação é o que dá sentido ao DNS round-robin: se ambos estivessem na mesma máquina, uma falha de hardware derrubaria os dois simultaneamente. Ambos expõem a porta 80 e são referenciados pelo mesmo hostname DNS. Não há Nginx — o DNS é o balanceador.

**Inventário + Transações (EC2-2)**
Esses dois serviços fazem chamadas gRPC frequentes ao Gerente de BD e publicam eventos no RabbitMQ. Colocá-los juntos reduz a latência das chamadas internas entre eles. Ambos são stateless e têm perfil de carga similar.

**Notificação + Agente de manutenção (EC2-3)**
O Serviço de notificação consome filas do RabbitMQ em loop contínuo. O Agente de manutenção também roda em loop contínuo e publica nessas mesmas filas. Agrupar os dois concentra o tráfego de mensageria assíncrona em uma instância.

**RabbitMQ (EC2-4)**
Message broker isolado em instância própria. RabbitMQ tem comportamento de I/O e memória diferente dos serviços Python — misturá-lo com outros processos causaria contenção de recursos.

**Gerente de BD + BDs principais (EC2-5)**
O componente mais crítico do sistema — único ponto de acesso a dados, com os 3 arquivos `.db` principais, locks e lógica de replicação. Roda em Java com JVM. Isolá-lo garante que nenhum outro processo interfira no gerenciamento de réplicas ou nos locks.

**ReplicaAgents + BDs réplicas (EC2-6)**
Todos os `ReplicaAgent`s ficam em uma única instância dedicada. Separar réplicas dos principais em máquinas distintas garante que uma falha de hardware em EC2-5 não derrube simultaneamente o principal e todas as réplicas.

### 8.2 Mapa de instâncias, processos e portas

| Instância | Processos | Porta local | Protocolo | Acessível de | Instância mínima | Instância recomendada |
| --- | --- | --- | --- | --- | --- | --- |
| **EC2-1a** | Gateway A | **80** | HTTP | Internet (0.0.0.0/0) | `t3.micro` | `t3.small` |
| **EC2-1b** | Gateway B | **80** | HTTP | Internet (0.0.0.0/0) | `t3.micro` | `t3.small` |
| **EC2-2** | Serviço de inventário | 8002 | HTTP | EC2-1a, EC2-1b | `t3.small` | `t3.medium` |
| **EC2-2** | Serviço de transações | 8003 | HTTP | EC2-1a, EC2-1b | — | — |
| **EC2-3** | Serviço de notificação | 8004 | HTTP | EC2-1a, EC2-1b | `t3.micro` | `t3.small` |
| **EC2-3** | Agente de manutenção | — | (sem porta) | — | — | — |
| **EC2-4** | RabbitMQ | **5672** | AMQP | EC2-2, EC2-3 | `t3.small` | `t3.small` |
| **EC2-4** | RabbitMQ Management UI | **15672** | HTTP | EC2-4 (127.0.0.1) | — | — |
| **EC2-5** | Gerente de BD | **50050** | gRPC | EC2-1a, EC2-1b, EC2-2, EC2-3 | `t3.medium` | `t3.large` |
| **EC2-6** | ReplicaAgent shard-A réplica-1 | 50100 | gRPC | EC2-5 | `t3.small` | `t3.medium` |
| **EC2-6** | ReplicaAgent shard-A réplica-2 | 50101 | gRPC | EC2-5 | — | — |
| **EC2-6** | ReplicaAgent shard-B réplica-1 | 50200 | gRPC | EC2-5 | — | — |
| **EC2-6** | ReplicaAgent shard-B réplica-2 | 50201 | gRPC | EC2-5 | — | — |
| **EC2-6** | ReplicaAgent shard-C réplica-1 | 50300 | gRPC | EC2-5 | — | — |
| **EC2-6** | ReplicaAgent shard-C réplica-2 | 50301 | gRPC | EC2-5 | — | — |

> **Regra geral:** apenas a porta 80 de EC2-1a e EC2-1b é pública. Todas as demais comunicações usam IPs privados da VPC.

### 8.3 Security Groups AWS

#### SG-1 — EC2-1a e EC2-1b (Gateways)

| Direção | Porta | Protocolo | Origem | Motivo |
| --- | --- | --- | --- | --- |
| Inbound | 80 | TCP | 0.0.0.0/0 | Acesso público via DNS round-robin |
| Inbound | 22 | TCP | IP fixo da equipe | SSH para administração |
| Outbound | tudo | tudo | 0.0.0.0/0 | Chamadas para EC2-2, EC2-3, EC2-5 |

#### SG-2 — EC2-2 (Inventário + Transações)

| Direção | Porta | Protocolo | Origem | Motivo |
| --- | --- | --- | --- | --- |
| Inbound | 8002 | TCP | SG-1 | Gateway → Inventário |
| Inbound | 8003 | TCP | SG-1 | Gateway → Transações |
| Inbound | 22 | TCP | IP fixo da equipe | SSH |
| Outbound | tudo | tudo | 0.0.0.0/0 | gRPC para EC2-5, AMQP para EC2-4 |

#### SG-3 — EC2-3 (Notificação + Agente)

| Direção | Porta | Protocolo | Origem | Motivo |
| --- | --- | --- | --- | --- |
| Inbound | 8004 | TCP | SG-1 | Gateway → Notificação |
| Inbound | 22 | TCP | IP fixo da equipe | SSH |
| Outbound | tudo | tudo | 0.0.0.0/0 | gRPC para EC2-5, AMQP para EC2-4 |

#### SG-4 — EC2-4 (RabbitMQ)

| Direção | Porta | Protocolo | Origem | Motivo |
| --- | --- | --- | --- | --- |
| Inbound | 5672 | TCP | SG-2, SG-3 | Publicação e consumo de eventos |
| Inbound | 22 | TCP | IP fixo da equipe | SSH |

A porta 15672 não é exposta externamente — acessar via SSH tunnel (ver seção 7.5).

#### SG-5 — EC2-5 (Gerente de BD)

| Direção | Porta | Protocolo | Origem | Motivo |
| --- | --- | --- | --- | --- |
| Inbound | 50050 | TCP | SG-1, SG-2, SG-3 | gRPC de todos os serviços |
| Inbound | 22 | TCP | IP fixo da equipe | SSH |
| Outbound | tudo | tudo | 0.0.0.0/0 | gRPC ApplyWrite/Ping para EC2-6 |

#### SG-6 — EC2-6 (ReplicaAgents)

| Direção | Porta | Protocolo | Origem | Motivo |
| --- | --- | --- | --- | --- |
| Inbound | 50100–50301 | TCP | SG-5 | ApplyWrite e Ping vindos do Gerente de BD |
| Inbound | 22 | TCP | IP fixo da equipe | SSH |

### 8.4 Configuração dos `config.yaml` por instância

```yaml
# config.yaml em EC2-1a e EC2-1b (Gateways — idêntico nas duas)
servidor:
  host: "0.0.0.0"
  porta: 80

servicos:
  inventario:
    host: "10.0.0.2"   # IP privado EC2-2
    porta: 8002
  transacoes:
    host: "10.0.0.2"
    porta: 8003
  notificacao:
    host: "10.0.0.3"   # IP privado EC2-3
    porta: 8004

gerente_bd:
  host: "10.0.0.5"     # IP privado EC2-5
  porta: 50050

fila_mensagens:
  host: "10.0.0.4"     # IP privado EC2-4
  porta: 5672
  usuario: "guest"
  senha: "guest"
```

```yaml
# config.yaml em EC2-5 (Gerente de BD)
gerente_bd:
  host: "0.0.0.0"
  porta: 50050
  qtd_max_replicas: 2
  replicas_host: "10.0.0.6"   # IP privado EC2-6
  replicas_porta_base: 50100

dados:
  diretorio: "./data"
```

```yaml
# config.yaml em EC2-2 e EC2-3 (serviços Python)
gerente_bd:
  host: "10.0.0.5"
  porta: 50050

fila_mensagens:
  host: "10.0.0.4"
  porta: 5672
  usuario: "guest"
  senha: "guest"
```

### 8.5 Acesso às instâncias via terminal

```bash
ssh -i chave-projeto.pem ec2-user@<IP-PUBLICO-EC2-1a>   # Gateway A
ssh -i chave-projeto.pem ec2-user@<IP-PUBLICO-EC2-1b>   # Gateway B
ssh -i chave-projeto.pem ec2-user@<IP-PUBLICO-EC2-2>    # Inventário + Transações
ssh -i chave-projeto.pem ec2-user@<IP-PUBLICO-EC2-3>    # Notificação + Agente
ssh -i chave-projeto.pem ec2-user@<IP-PUBLICO-EC2-4>    # RabbitMQ
ssh -i chave-projeto.pem ec2-user@<IP-PUBLICO-EC2-5>    # Gerente de BD
ssh -i chave-projeto.pem ec2-user@<IP-PUBLICO-EC2-6>    # ReplicaAgents
```

#### Acesso à Management UI do RabbitMQ via SSH tunnel

```bash
ssh -i chave-projeto.pem -L 15672:localhost:15672 ec2-user@<IP-PUBLICO-EC2-4>
# Depois abrir no browser: http://localhost:15672
# Login padrão: guest / guest
```

### 8.6 Ordem de inicialização no deploy

```bash
# Passo 1 — EC2-6: ReplicaAgents (devem estar prontos antes do Gerente de BD)
ssh -i chave-projeto.pem ec2-user@<EC2-6>
cd scd-inventario
nohup bash scripts/start_all_replicas.sh > logs/replicas.log 2>&1 &

# Passo 2 — EC2-5: Gerente de BD (todos os serviços dependem dele)
ssh -i chave-projeto.pem ec2-user@<EC2-5>
cd scd-inventario && nohup bash scripts/start_db_manager.sh > logs/db_manager.log 2>&1 &

# Passo 3 — EC2-4: RabbitMQ
ssh -i chave-projeto.pem ec2-user@<EC2-4>
sudo systemctl start rabbitmq-server

# Passo 4 — EC2-2: Inventário e Transações
ssh -i chave-projeto.pem ec2-user@<EC2-2>
cd scd-inventario
nohup bash scripts/start_inventario.sh --port 8002 > logs/inventario.log 2>&1 &
nohup bash scripts/start_transacoes.sh --port 8003 > logs/transacoes.log 2>&1 &

# Passo 5 — EC2-3: Notificação e Agente
ssh -i chave-projeto.pem ec2-user@<EC2-3>
cd scd-inventario
nohup bash scripts/start_notificacao.sh --port 8004 > logs/notificacao.log 2>&1 &
nohup bash scripts/start_agente.sh > logs/agente.log 2>&1 &

# Passo 6 — EC2-1a e EC2-1b: Gateways (últimos — só sobem depois que os serviços internos estão prontos)
ssh -i chave-projeto.pem ec2-user@<EC2-1a>
cd scd-inventario && nohup bash scripts/start_gateway.sh --port 80 > logs/gateway.log 2>&1 &

ssh -i chave-projeto.pem ec2-user@<EC2-1b>
cd scd-inventario && nohup bash scripts/start_gateway.sh --port 80 > logs/gateway.log 2>&1 &
```

Para reanexar e ver output de um processo:

```bash
# Opção tmux (recomendado para a demo)
tmux new-session -d -s db_manager 'bash scripts/start_db_manager.sh'
tmux attach -t db_manager
```

#### Configuração do DNS round-robin

```dns
; Zona DNS — dois A records para o mesmo hostname
api.scd-inventario.local.  60  IN  A  <IP-PUBLICO-EC2-1a>
api.scd-inventario.local.  60  IN  A  <IP-PUBLICO-EC2-1b>
```

## 9. Estrutura de diretórios

```dir
scd-inventario/
├── config.template.yaml
├── config.yaml                  # gerado no primeiro boot (no .gitignore)
├── README.md
│
├── scripts/
│   ├── start_gateway.sh
│   ├── start_inventario.sh
│   ├── start_transacoes.sh
│   ├── start_notificacao.sh
│   ├── start_agente.sh
│   ├── start_db_manager.sh
│   ├── start_all_replicas.sh    # lê qtd_max_replicas do config.yaml e sobe 3 shards × N réplicas
│   ├── stop_all_replicas.sh
│   └── start_replica_agent.sh   # args: --shard <shard_id> --porta <porta>
│
├── shared/
│   └── config.py
│
├── gateway/
│   ├── main.py
│   └── auth.py
│
├── services/
│   ├── inventory/
│   │   ├── main.py
│   │   └── db_client.py
│   ├── order/
│   │   ├── main.py
│   │   └── db_client.py
│   ├── notification/
│   │   ├── main.py
│   │   ├── consumer.py
│   │   └── db_client.py
│   └── agente/
│       └── worker.py
│
├── db_manager/
│   ├── src/main/java/
│   │   ├── DBManagerService.java
│   │   ├── ConfigLoader.java
│   │   ├── ShardRouter.java
│   │   ├── ReplicationManager.java   # chama ApplyWrite gRPC nos ReplicaAgents remotos
│   │   └── FailoverController.java
│   ├── src/main/resources/
│   │   ├── config.template.yaml
│   │   └── seed.sql
│   ├── proto/
│   │   ├── dbmanager.proto
│   │   └── replica.proto             # interface interna do ReplicaAgent
│   └── pom.xml
│
├── replica_agent/                    # processo Java leve — roda em EC2-6
│   ├── src/main/java/
│   │   ├── ReplicaAgentService.java  # implementa ApplyWrite e Ping
│   │   └── ConfigLoader.java
│   ├── proto/
│   │   └── replica.proto
│   └── pom.xml
│
├── proto/
│   ├── dbmanager.proto
│   └── replica.proto
│
├── clients/
│   ├── http/
│   │   ├── _http.py               # função _request compartilhada (stdlib apenas)
│   │   ├── auth_http.py           # POST /auth/register, POST /auth/login
│   │   ├── inventory_http.py      # /products, /watchlist, /flash-offers
│   │   ├── transaction_http.py    # /orders
│   │   ├── notification_http.py   # GET /notifications, PATCH /notifications/read
│   │   └── admin_http.py          # /admin/db-status, /admin/promote-replica, /health
│   ├── buyer_cli.py               # CLI do comprador: menu, prompts e polling
│   ├── seller_cli.py              # CLI do vendedor: menu, prompts e polling
│   └── admin_cli.py               # CLI do admin: menu, prompts e polling
│
├── demo/
│   ├── simulate_load.py
│   └── scenario.sh
│
└── data/                        # criado pelo Gerente de BD em EC2-5
    ├── shard_a/
    ├── shard_b/
    └── shard_c/
```
