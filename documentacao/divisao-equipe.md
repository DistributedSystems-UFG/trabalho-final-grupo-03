# Divisão do Código-Fonte — SCD Inventário

**Disciplina:** Software Concorrente e Distribuído — UFG/INF 2026.1
**Professor:** Fábio Moreira Costa
**Entrega:** 30/06/2026

Este documento divide a implementação em 4 partes de complexidade e volume comparáveis, cada uma atribuível a um integrante da equipe. A divisão segue fronteiras arquiteturais naturais do sistema — cada parte corresponde a uma camada ou subsistema coeso, minimizando dependências cruzadas durante o desenvolvimento paralelo.

| Parte | Responsável | Tema |
| --- | --- | --- |
| 1 | Leonardo Côrtes | Gerente de BD — núcleo de dados (Java/gRPC) |
| 2 | Mateus de Almeida | ReplicaAgent, Agente de Manutenção e Infraestrutura |
| 3 | Matheus Geraldino | Serviços de negócio (Inventário, Transações, Notificação) |
| 4 | Deivison Oliveira | Gateway, Clientes CLI e Demonstração |

A leve diferença de linhas entre as partes 2/3 e a parte 4 é compensada pela maior densidade de lógica concorrente nas Partes 1 e 2 (locks, replicação, failover) frente ao código mais repetitivo (porém extenso) de CLIs e proxy HTTP na Parte 4.

---

## Parte 1 — Gerente de BD: núcleo de dados (Java/gRPC)

**Tema central:** o único componente do sistema autorizado a acessar bancos de dados. Implementa particionamento por categoria, controle de concorrência via locks, idempotência, replicação e failover automático.

### Arquivos

```dir
db_manager/
├── src/main/java/com/scd/dbmanager/
│   ├── DBManagerMain.java          — ponto de entrada, sobe servidor gRPC
│   ├── DBManagerService.java       — implementação dos RPCs (Read/Write/HealthCheck/...)
│   ├── ShardRouter.java            — mapeia category → shard_id
│   ├── ShardDatabase.java          — encapsula conexão JDBC de um shard principal
│   ├── ReplicationManager.java     — propaga writes para ReplicaAgents via gRPC
│   ├── FailoverController.java     — healthcheck periódico e promoção de réplica
│   ├── ReplicaTopology.java        — deriva portas de réplica automaticamente
│   └── ConfigLoader.java           — bootstrap e leitura de config.yaml
├── src/main/resources/
│   ├── config.template.yaml
│   └── seed.sql                    — schema completo (tabelas globais e particionadas)
├── proto/
│   ├── dbmanager.proto             — interface pública (Read, Write, GetStatus, ...)
│   └── replica.proto               — interface interna do ReplicaAgent (referência)
├── pom.xml
└── scripts/start_db_manager.sh
```

### O que esta parte cobre

- Modelagem do schema SQLite (tabelas globais replicadas vs. particionadas por shard).
- `ShardRouter`: roteamento determinístico de categoria de produto para shard (`shard_a`/`shard_b`/`shard_c`).
- Controle de concorrência: `ReentrantLock` por `product_id` dentro de cada shard, e lock de menor granularidade para escritas em tabelas globais.
- Idempotência de escritas via `origin_id` (tabela `processed_writes`).
- Reads cross-shard (paralelos nos 3 principais, com merge de resultados) e reads direcionados por categoria.
- Replicação assíncrona fire-and-forget para os `ReplicaAgent`s remotos (réplicas nunca bloqueiam a confirmação ao cliente).
- Failover automático: detecção de falha via healthcheck periódico, promoção de réplica, log de writes pendentes em memória para replay.
- Derivação automática da topologia de portas de réplica (`replicas_porta_base` + fórmula por shard/índice) — elimina a necessidade do usuário listar portas manualmente no `config.yaml`.
- Protobuf da interface pública (`dbmanager.proto`).

### Pré-requisitos para iniciar este trabalho

Nenhum — esta é a base da qual as demais partes dependem (via gRPC). Pode ser desenvolvida isoladamente com clientes gRPC gerados pelo protoc e testada com `grpcurl` antes da integração com os serviços Python.

---

## Parte 2 — ReplicaAgent, Agente de Manutenção e Infraestrutura

**Tema central:** os processos de suporte que sustentam a disponibilidade do sistema (réplicas remotas e tarefas periódicas de manutenção), além dos scripts de orquestração de todos os componentes.

### Arquivos

```dir
replica_agent/
├── src/main/java/com/scd/replica/
│   ├── ReplicaAgentService.java    — implementa ApplyWrite e Ping, contém main()
│   ├── ReplicaTopology.java        — mesma fórmula de portas usada pelo Gerente de BD
│   └── ConfigLoader.java           — bootstrap, valida shard/index contra a topologia
├── src/main/resources/
│   ├── config.template.yaml
│   └── seed.sql                    — mesma estrutura de tabelas do principal
├── proto/replica.proto
└── pom.xml

services/agente/
└── worker.py                       — loop contínuo: alerta de estoque, watchlist, flash offers

scripts/
├── start_replica_agent.sh          — sobe uma instância (--shard --index)
├── start_all_replicas.sh           — sobe todas as instâncias automaticamente
├── stop_all_replicas.sh
├── start_db_manager.sh             — (referenciado também na Parte 1)
└── start_agente.sh

shared/
└── config.py                       — bootstrap e leitura de config.yaml (Python)
```

### O que esta parte cobre

- `ReplicaAgentService`: processo Java leve, sem lógica de negócio, que persiste o que o Gerente de BD enviar via `ApplyWrite`, com idempotência local e healthcheck via `Ping`.
- Cálculo de porta derivada (mesma convenção da Parte 1, replicada aqui por independência de processo).
- `worker.py` (Agente de Manutenção): três tarefas independentes em loop — alerta de estoque baixo (`stock.low`), verificação de watchlist de preço (`price.alert`) e expiração de ofertas relâmpago — todas publicando no RabbitMQ e usando o padrão de "flag + reset" (`alerta_enviado`, `notified`) para evitar alertas duplicados.
- Scripts de orquestração: bootstrap de múltiplas réplicas com um único comando, parsing de `qtd_max_replicas` do `config.yaml`, gestão de PIDs para encerramento limpo.
- `shared/config.py`: ponto único de configuração para todos os serviços Python, com bootstrap automático idêntico ao `ConfigLoader.java`.

### Pré-requisitos para iniciar este trabalho

Depende do protocolo gRPC definido na Parte 1 (`replica.proto`) para implementar `ApplyWrite`/`Ping`. Pode ser desenvolvida em paralelo assim que o `.proto` estiver congelado, usando um Gerente de BD "fake" para testes.

---

## Parte 3 — Serviços de negócio (Inventário, Transações, Notificação)

**Tema central:** a lógica de domínio do marketplace — produtos, pedidos, watchlist, ofertas relâmpago e o pipeline de notificações assíncronas.

### Arquivos

```dir
services/inventory/
├── main.py        — endpoints REST: produtos, watchlist, flash offers, admin
└── db_client.py    — cliente gRPC para o Gerente de BD

services/order/
├── main.py         — endpoint de compra direta com reserva atômica de estoque
└── db_client.py

services/notification/
├── main.py          — endpoints de polling (GET/PATCH /notifications)
├── consumer.py       — consumer RabbitMQ (stock.low, order.completed, price.alert, flash.offer)
└── db_client.py

scripts/
├── start_inventario.sh
├── start_transacoes.sh
└── start_notificacao.sh
```

### O que esta parte cobre

- CRUD de produtos com autorização por dono (`seller_id`).
- Hashing de senha (SHA-256) e geração de token de autenticação.
- Watchlist de preço e ofertas relâmpago: criação, consulta, cálculo de `promo_price` a partir de `discount_pct`.
- Compra direta com **decremento atômico de estoque** via `UPDATE ... WHERE quantity >= ?` — o conflito de concorrência é detectado pelo próprio SQL, sem lock explícito no nível do serviço, delegando a serialização real ao lock por `product_id` do Gerente de BD.
- Publicação de eventos no RabbitMQ (`order.completed`, `flash.offer`) com exchange `topic` durável.
- Consumer RabbitMQ com fila exclusiva e reconexão automática, despachando cada evento para o handler correspondente e persistindo notificações via Gerente de BD.
- Endpoint de polling para notificações com marcação de lidas em lote.

### Pré-requisitos para iniciar este trabalho

Depende do `dbmanager.proto` (Parte 1) para os clientes gRPC, e de um RabbitMQ rodando localmente (`docker run rabbitmq` ou instalação nativa) para testar a publicação/consumo de eventos. Pode ser desenvolvida com mocks do Gerente de BD enquanto a Parte 1 não estiver pronta.

---

## Parte 4 — Gateway, Clientes CLI e Demonstração

**Tema central:** a porta de entrada única do sistema e as ferramentas de interação humana e de carga usadas para validar e apresentar o projeto.

### Arquivos

```dir
gateway/
├── main.py         — proxy stateless para os serviços internos, injeta X-User-Id/Role
└── auth.py          — resolução de token via gRPC, dependências FastAPI por role

clients/
├── http/
│   ├── _http.py             — função _request compartilhada (stdlib apenas, sem venv)
│   ├── auth_http.py         — POST /auth/register, POST /auth/login
│   ├── inventory_http.py    — /products, /watchlist, /flash-offers
│   ├── transaction_http.py  — /orders
│   ├── notification_http.py — GET /notifications, PATCH /notifications/read
│   └── admin_http.py        — /admin/db-status, /admin/promote-replica, /health
├── buyer_cli.py     — CLI interativo do comprador (compra, watchlist, notificações)
├── seller_cli.py    — CLI interativo do vendedor (produtos, vendas, flash offers)
└── admin_cli.py     — CLI privilegiado (status de shards, promoção manual de réplica)

demo/
├── simulate_load.py — script de carga concorrente (ThreadPoolExecutor)
└── scenario.sh       — roteiro de demonstração passo a passo para a banca

scripts/
└── start_gateway.sh
```

### O que esta parte cobre

- Gateway HTTP stateless: resolve token a cada requisição (sem sessão), repassa para o serviço interno correto preservando método/query/body, e injeta `X-User-Id`/`X-User-Role` para os serviços downstream.
- Dependências FastAPI por role (`require_buyer`, `require_seller`, `require_admin`, `require_buyer_or_seller`) com fábrica genérica (`require_role(*roles)`).
- **Módulos HTTP** (`clients/http/`): uma função por endpoint, stdlib pura (`urllib`, `json`, `os`), sem dependências externas — executáveis diretamente com `python <módulo>.py` como smoke test, sem venv.
- **CLIs** (`clients/`): importam os módulos HTTP e implementam menus interativos com polling de notificações em thread separada, resolução de produto por **nome** (em vez de exigir UUID do usuário), com desambiguação por lista numerada quando há múltiplos resultados.
- CLI admin com visualização formatada da topologia de shards e fluxo de confirmação para promoção manual de réplica.
- Script de carga (`simulate_load.py`) que registra usuários, cadastra produtos em todas as categorias, dispara compras concorrentes via `ThreadPoolExecutor`, testa watchlist e flash offers, e verifica notificações — útil para validar consistência sob concorrência.
- Roteiro de demonstração (`scenario.sh`) em 9 etapas formatadas para apresentação à banca, incluindo teste de 5 compradores concorrentes no mesmo produto (conflitos 409 esperados).

### Pré-requisitos para iniciar este trabalho

Depende dos endpoints REST definidos nas Partes 1–3 estarem ao menos especificados (ver `README.md`, seção "Endpoints principais") para que o proxy do Gateway e os CLIs sejam codificados contra um contrato estável, mesmo que os serviços ainda não estejam implementados (pode-se mockar com um servidor HTTP simples durante o desenvolvimento paralelo).

---

## Pontos de integração entre as partes

| Interface | Entre quais partes | Arquivo de contrato |
| --- | --- | --- |
| gRPC `DBManager` | Parte 1 ↔ Partes 2 e 3 | `proto/dbmanager.proto` |
| gRPC `ReplicaAgent` | Parte 1 ↔ Parte 2 | `proto/replica.proto` |
| REST interno | Parte 4 ↔ Parte 3 | seção "Endpoints REST" do `implementacao.md` |
| RabbitMQ (eventos) | Parte 2 (agente) e Parte 3 (inventory/order) → Parte 3 (notification) | seção "Payloads dos eventos RabbitMQ" do `implementacao.md` |
| `config.yaml` | Todas | `config.template.yaml` (raiz) |

Recomenda-se que os `.proto` (Parte 1) e a tabela de endpoints REST sejam congelados **antes** do início do desenvolvimento paralelo, já que são o contrato que permite que as 4 partes avancem simultaneamente com mocks.

## Ordem sugerida de integração

1. Parte 1 sozinha — testável via `grpcurl` ou client gRPC mínimo.
2. Parte 2 integrada à Parte 1 — sobe réplicas reais, testa failover.
3. Parte 3 integrada à Parte 1 — testa CRUD de produtos e compra via `curl` direto nos serviços (sem Gateway).
4. Parte 4 integrada às Partes 1–3 — sistema completo, ponta a ponta, acessível via CLI.
