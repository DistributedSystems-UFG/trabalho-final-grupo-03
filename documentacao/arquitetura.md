# Documentação de Arquitetura — Sistema de Controle de Inventário

**Disciplina:** Software Concorrente e Distribuído — UFG/INF 2026.1  
**Professor:** Fábio Moreira Costa  
**Entrega:** 30/06/2026

## 1. Visão geral

O sistema é um **marketplace** onde vendedores listam produtos com quantidade própria e compradores podem adquiri-los de duas formas: escolhendo um vendedor específico (interação síncrona REST) ou utilizando o sistema de **compra inteligente** (watchlist de preço e ofertas relâmpago, via pub-sub assíncrono com RabbitMQ). Um admin supervisiona o sistema via CLI privilegiado.

### Princípio central de dados

> **Para qualquer serviço do sistema, existe exatamente um banco de dados: o Gerente de BD.**

Nenhum serviço conhece a existência de shards, BD principal, réplicas, ou quantos bancos estão rodando. Todo acesso a dados — leitura ou escrita — é feito via chamada gRPC ao Gerente de BD. A topologia interna de bancos, incluindo o particionamento por categoria, é detalhe de implementação exclusivo do Gerente de BD.

### Preparação para load balancing nos Gateways

Os dois Gateways são expostos publicamente em portas distintas em instâncias EC2 separadas. Um registro DNS do tipo **A** aponta o mesmo hostname para os dois IPs públicos — o DNS faz round-robin entre eles de forma transparente para os clientes. Os CLIs e demais clientes apontam apenas para o hostname (ex.: `api.scd-inventario.local`), sem conhecer IPs ou portas individuais.

Cada Gateway é **stateless** — não armazena nenhum estado local entre requisições. Toda autenticação é resolvida via token no header consultando o Gerente de BD, e todo dado trafega pelos serviços internos. Isso garante que múltiplas instâncias do Gateway funcionem atrás do DNS sem nenhuma alteração de código. O `GET /health` exposto por cada Gateway serve como endpoint de verificação de disponibilidade.

### Requisitos atendidos

| Requisito | Como é atendido |
| --- | --- |
| Serviço acessível a múltiplos clientes na Internet | Dois Gateways HTTP expostos publicamente via DNS round-robin, deploy no AWS EC2 |
| Componentes distribuídos integrados | Gateway (×2), Serviço de inventário, Serviço de transações, Serviço de notificação, Agente de manutenção, Gerente de BD, ReplicaAgents |
| Acessos concorrentes a recursos compartilhados | Lock por `product_id` no Gerente de BD; decremento atômico de estoque via `UPDATE ... WHERE quantity >= ?`; controle de ACK no RabbitMQ |
| Processamento no lado servidor concorrente com acessos | Agente de manutenção em processo separado, independente das requisições REST |
| Interação remota síncrona e assíncrona | REST síncrono (cliente↔sistema) + RabbitMQ assíncrono (pub-sub) + gRPC síncrono (serviços↔Gerente de BD) |
| Replicação e particionamento | BD particionado em 3 shards por categoria; cada shard com N réplicas SQLite remotas, gerenciadas pelo Gerente de BD via gRPC leve |
| Consistência e disponibilidade | Failover automático com promoção de réplica e replay de writes pendentes; idempotência por `origin_id` |

## 2. Componentes

### 2.1 API Gateway (Python · FastAPI)

Único ponto de entrada para todos os clientes externos. Dois Gateways rodam em instâncias EC2 separadas (EC2-1a e EC2-1b), cada um escutando na porta 8080. Um registro DNS A aponta o mesmo hostname para os dois IPs públicos — os clientes usam apenas o hostname e o DNS faz round-robin entre as instâncias.

> Os clientes falam apenas com o hostname DNS — não sabem dos IPs individuais nem das instâncias.

O TTL curto (60 s) garante que, se um Gateway cair, os clientes migrem para o outro no próximo ciclo de resolução DNS. Quando o Gateway volta, entra automaticamente no round-robin. Nenhuma linha de código do Gateway muda — o serviço já é stateless por design; o DNS apenas explora isso.

### 2.2 Serviço de inventário (Python · FastAPI)

Gerencia o catálogo de produtos e o estoque de cada vendedor.

- CRUD de produtos por vendedor
- Toda leitura e escrita feita via `ReadRequest` / `WriteRequest` gRPC ao Gerente de BD
- Ao cadastrar um produto, inscreve automaticamente o vendedor na fila RabbitMQ da categoria correspondente
- Gerencia **watchlist** de preço: criação, consulta e remoção de entradas por comprador
- Gerencia **ofertas relâmpago**: criação de promoções com duração limitada por vendedor

### 2.3 Serviço de transações (Python · FastAPI)

Processa transações de compra.

- Compra direta: lê a `category` do produto via Gerente de BD → lê estoque (mesmo shard) → reserva via Gerente de BD → confirma → publica evento `order.completed`
- Em caso de conflito (`WriteAck.success = false`), retorna 409 ao cliente

### 2.4 Serviço de notificação (Python · FastAPI)

Consome filas de eventos do RabbitMQ e persiste notificações no BD via Gerente de BD. Também expõe endpoints HTTP para que o Gateway sirva notificações aos CLIs via polling.

Os CLIs recebem notificações por **polling** — requisitando periodicamente `GET /notifications` ao Gateway, que repassa ao Serviço de notificação, que consulta o Gerente de BD e retorna as notificações não lidas. Após exibir, o CLI chama `PATCH /notifications/read` para marcar as notificações como lidas.

Eventos consumidos do RabbitMQ:

- Consome `stock.low` → persiste alerta para o vendedor correspondente
- Consome `order.completed` → persiste notificação para comprador e vendedor
- Consome `price.alert` → persiste notificação para o comprador que cadastrou a watchlist
- Consome `flash.offer` → busca compradores com interesse no produto/categoria via Gerente de BD e persiste notificação para cada um

O RabbitMQ não acessa o BD diretamente — ele apenas entrega eventos ao Serviço de notificação, que é o responsável por persistir e disponibilizar as notificações via Gerente de BD.

### 2.5 Agente de manutenção (Python · processo dedicado)

Processo separado que roda em loop contínuo, completamente independente dos servidores HTTP. Iniciado por script shell próprio (`scripts/start_agente.sh`).

- **Alerta de estoque baixo:** lê produtos via Gerente de BD periodicamente; para cada produto abaixo de `alerta_quantidade` com `alerta_enviado = 0`, publica `stock.low` e atualiza `alerta_enviado = 1`; quando o estoque volta acima do limiar, redefine `alerta_enviado = 0` para reabilitar alertas futuros
- **Watchlist de preço:** lê entradas de watchlist e o preço atual de cada produto via Gerente de BD (ambos no mesmo shard, pois `watchlist` segue o shard do produto); para cada entrada com `price <= max_price` e `notified = 0`, publica `price.alert` e atualiza `notified = 1`; quando o preço sobe acima do limiar, redefine `notified = 0` para reabilitar o alerta
- **Expiração de ofertas relâmpago:** lê `flash_offers` com `expires_at` vencido e `status = 'active'` via Gerente de BD; atualiza `status = 'expired'` e restaura o preço original do produto

### 2.6 Gerente de banco de dados (Java · gRPC)

**Único componente do sistema autorizado a acessar bancos de dados.**

Do ponto de vista dos demais serviços, o Gerente de BD *é* o banco de dados — uma abstração completa. Internamente, ele gerencia 3 shards SQLite particionados por categoria de produto. Cada shard tem um BD principal (local, na mesma máquina EC2-5) e réplicas remotas (em EC2-6), acessadas via um **processo leve de réplica** (`ReplicaAgent`) que expõe uma interface gRPC mínima. Isso é invisível para qualquer outro componente.

#### Particionamento por categoria

O Gerente de BD implementa um `ShardRouter` interno que direciona cada operação ao shard correto com base no campo `category` presente no `WriteRequest` ou `ReadRequest`:

| Shard | Categorias |
| --- | --- |
| Shard A | Eletrônicos, Informática, Telefonia |
| Shard B | Roupas, Calçados, Acessórios |
| Shard C | Casa, Esporte, Outros |

**Tabelas globais** (`users`, `notifications`) são replicadas nos 3 shards — cada shard contém uma cópia completa e atualizada dessas tabelas. Writes em tabelas globais são aplicados nos 3 shards em paralelo pelo Gerente de BD, de forma transparente. Isso elimina delegação inter-shard e garante que qualquer shard possa resolver uma FK de `user_id` localmente.

**Tabelas particionadas** (`products`, `orders`, `watchlist`, `flash_offers`) residem exclusivamente no shard correspondente à `category` do produto. `watchlist` e `flash_offers` seguem o shard do produto que referenciam.

Reads que cruzam shards (ex.: busca geral de produtos) são executados em paralelo nos 3 shards e os resultados são mesclados antes de retornar ao serviço. Cada shard mantém seu próprio conjunto de réplicas remotas conforme `qtd_max_replicas`. O failover é gerenciado independentemente por shard.

**Interface exposta (gRPC) — para os serviços:**

| RPC | Descrição |
| --- | --- |
| `Read` | executa uma query de leitura e retorna os resultados |
| `Write` | executa uma operação de escrita com garantia de idempotência |
| `HealthCheck` | retorna status geral dos bancos internos |
| `GetStatus` | retorna topologia atual por shard — qual é o principal, quantas réplicas (uso do admin via `/admin`) |
| `PromoteReplica` | força promoção de uma réplica específica em um shard (uso do admin via `/admin`) |

**Interface do ReplicaAgent (gRPC interno) — somente o Gerente de BD a usa:**

| RPC | Descrição |
| --- | --- |
| `ApplyWrite` | recebe um `WriteRequest` e o aplica no SQLite local da réplica |
| `Ping` | healthcheck leve; retorna `ok` se o processo está vivo e o arquivo `.db` acessível |

O `ReplicaAgent` é um processo Java leve, sem lógica de negócio, sem ShardRouter, sem locks — apenas persiste o que o Gerente de BD enviar e responde ao Ping. Cada réplica de cada shard corresponde a uma instância do `ReplicaAgent` em EC2-6, identificada por porta distinta.

**Responsabilidades internas (opacas aos serviços):**

- Recebe `ReadRequest` e `WriteRequest`, roteia ao shard correto via `ShardRouter` com base no campo `category`
- **Reads sempre vão ao BD principal do shard** — réplicas não servem reads; existem exclusivamente para failover
- Writes em tabelas globais são aplicados nos 3 principais em paralelo; o `WriteAck` é retornado após confirmação nos 3
- Para writes em tabelas particionadas: aplica no BD principal do shard correto, replica assincronamente nos `ReplicaAgent`s remotos via gRPC `ApplyWrite`, garante idempotência via `origin_id`
- Mantém lock por `product_id` dentro do shard para serializar escritas concorrentes no mesmo produto; writes em tabelas globais usam um lock global de menor granularidade por shard
- Executa healthcheck periódico em todos os BDs internos (`Ping` gRPC nos `ReplicaAgent`s; verificação direta nos arquivos `.db` locais)
- Ao detectar retorno de BD principal após failover, executa replay dos writes pendentes registrados durante a ausência, depois rebaixa o BD recuperado a réplica
- Lê `config.yaml` via `ConfigLoader` para obter `qtd_max_replicas`, `dados.diretorio` e os endereços dos `ReplicaAgent`s

> A Réplica 1 não é destituída ao retorno do original. Ela já está atualizada e em operação como principal. O BD original, após sincronizado via replay, contribui como réplica — preservando todos os seus dados sem desperdiçar um nó já populado.

## 3. Modelos de interação

### 3.1 Cliente-servidor (REST síncrono)

Usado em todas as interações dos usuários com o sistema:

```markdown
Comprador / Vendedor / Admin → HTTP → API Gateway → Serviço correspondente
```

O cliente aguarda a resposta HTTP antes de continuar (bloqueante).

**Polling de notificações:**

```markdown
CLI → GET /notifications → Gateway → Serviço de notificação
  → ReadRequest (category=global) → Gerente de BD
  → retorna lista de notificações não lidas → CLI exibe

CLI → PATCH /notifications { ids: [...] } → Gateway → Serviço de notificação
  → WriteRequest (category=global) → Gerente de BD (atualiza read = 1)
```

O intervalo de polling é configurado no lado do cliente (argumento `--poll-interval` do CLI, padrão 5 segundos).

### 3.2 Pub-sub (RabbitMQ · assíncrono) — Compra Inteligente

#### Watchlist de preço

```markdown
Comprador → POST /watchlist { product_id, max_price }
  → Gateway autentica, resolve category do produto via Gerente de BD
  → Serviço de inventário → WriteRequest (category=<categoria do produto>)
    → Gerente de BD persiste em watchlist no shard do produto

Agente de manutenção (loop a cada intervalo_segundos, por shard):
  → ReadRequest (category=<shard>) → lê watchlist JOIN products (do principal do shard)
  → se price <= max_price e notified = 0:
      → publica price.alert no RabbitMQ
      → WriteRequest → atualiza notified = 1
  → se price > max_price e notified = 1:
      → WriteRequest → redefine notified = 0

Serviço de notificação consome price.alert
  → WriteRequest (category=global) → persiste notificação para o buyer_id do payload
  → comprador recebe via polling
```

#### Oferta relâmpago

```markdown
Vendedor → POST /flash-offers { product_id, discount_pct, duration_minutes }
  → Serviço de inventário lê category do produto via Gerente de BD
  → calcula promo_price, persiste flash_offers, atualiza price do produto (mesmo shard)
  → publica flash.offer no RabbitMQ

Serviço de notificação consome flash.offer
  → ReadRequest (category=<category do payload>) → busca watchlist do produto/categoria
  → para cada buyer_id encontrado:
      → WriteRequest (category=global) → persiste notificação
  → compradores recebem via polling

Agente de manutenção (loop por shard):
  → ReadRequest → lê flash_offers com expires_at vencido e status='active'
  → WriteRequest → atualiza status='expired', restaura original_price no produto
```

**Mapa completo de eventos:**

| Evento | Publicador | Consumidor |
| --- | --- | --- |
| `stock.low` | Agente de manutenção | Serviço de notificação |
| `order.completed` | Serviço de transações | Serviço de notificação |
| `price.alert` | Agente de manutenção | Serviço de notificação |
| `flash.offer` | Serviço de inventário | Serviço de notificação |

### 3.3 gRPC (acesso a dados · Java)

**Todo acesso a dados passa por aqui, sem exceção:**

```markdown
Qualquer serviço Python → ReadRequest (category, sql, params) → Gerente de BD
  → category preenchida: roteia para o shard correspondente; read vai ao principal local
  → category vazia: executa em paralelo nos 3 principais e mescla resultados
  → retorna ReadResult

Qualquer serviço Python → WriteRequest (category, sql, params, origin_id, product_id) → Gerente de BD
  → category = "global": aplica nos 3 principais em paralelo, aguarda confirmação nos 3
  → category específica: aplica no principal do shard; replica assincronamente nos ReplicaAgents remotos via gRPC
  → retorna WriteAck { success, error }

Gerente de BD → ReplicaAgent (EC2-6, gRPC interno):
  → ApplyWrite: propaga o write para a réplica remota (assíncrono, best-effort com retry)
  → Ping: healthcheck periódico; falha aciona failover
```

Os serviços **não recebem nem lidam com conexões de banco**. Não há `sqlite3`, JDBC, nem qualquer driver de BD nos serviços Python — apenas clientes gRPC gerados pelo protoc.

## 4. Dados e persistência

### 4.1 Banco de dados

**SQLite** — um arquivo `.db` por instância (shard principal em EC2-5 + réplicas remotas em EC2-6 via `ReplicaAgent`), gerenciado exclusivamente pelo Gerente de BD. O schema é idêntico em todos os arquivos; o que os diferencia é o subconjunto de dados que cada um armazena, determinado pelo `ShardRouter`.

**Classificação das tabelas:**

| Tabela | Tipo | Onde reside |
| --- | --- | --- |
| `users` | Global | replicada nos 3 shards (principal + réplicas) |
| `notifications` | Global | replicada nos 3 shards (principal + réplicas) |
| `products` | Particionada | shard da `category` do produto |
| `orders` | Particionada | shard da `category` do produto |
| `watchlist` | Particionada | shard da `category` do produto referenciado |
| `flash_offers` | Particionada | shard da `category` do produto referenciado |

> **Foreign keys entre tabelas globais e particionadas** são válidas dentro de cada shard porque todas as tabelas globais estão presentes em todos os shards. Uma FK de `orders.buyer_id → users(id)` é sempre resolvível localmente, independentemente do shard em que o pedido reside.

O banco é inicializado pelo Gerente de BD na primeira execução a partir do `seed.sql`, localizado em `db_manager/src/main/resources/`. Réplicas são inicializadas como cópia do principal — o `seed.sql` nunca é executado diretamente nelas.

### 4.2 Controle de concorrência

Por ser o único escritor, o Gerente de BD centraliza todo o controle de concorrência. Um `ReentrantLock` por `product_id` serializa escritas concorrentes no mesmo produto dentro do shard correto, sem bloquear operações em produtos distintos. Writes em tabelas globais usam um lock global de baixa contenção, aplicado em cada shard independentemente.

#### Controle de duplicação de alertas

Tanto o `alerta_enviado` (estoque baixo) quanto o `notified` (watchlist) seguem o mesmo padrão: o campo é `0` quando o alerta está habilitado e `1` quando já foi enviado no episódio atual. O Agente de manutenção redefine o campo para `0` quando a condição deixa de ser verdadeira, reabilitando o alerta para episódios futuros.

### 4.3 Replicação

Gerenciada inteiramente pelo Gerente de BD, por shard, invisível aos serviços:

- Após aplicar um write em tabela particionada no principal local, dispara `ApplyWrite` gRPC em paralelo para cada `ReplicaAgent` do shard (em EC2-6)
- Writes em tabelas globais são aplicados nos 3 principais em paralelo; `WriteAck` retornado após confirmação nos 3
- `WriteAck` para tabelas particionadas retornado após confirmação no principal (não aguarda réplicas)
- Writes pendentes em réplicas com falha são registrados em memória e reaplicados quando o `ReplicaAgent` volta a responder ao `Ping`
- **Reads nunca vão para réplicas** — as réplicas existem exclusivamente para failover

### 4.4 Replay após failover

O Gerente de BD mantém um log em memória de todos os `WriteRequest` aplicados no principal após um failover. Quando o BD original é detectado como recuperado via healthcheck:

```markdown
1. Gerente de BD pausa novas escritas no shard por um instante (lock global do shard)
2. Itera o log de writes pendentes em ordem cronológica
3. Aplica cada write no BD recuperado via ApplyWrite gRPC, pulando os já idempotentes (origin_id duplicado)
4. BD recuperado entra no pool como réplica (seu ReplicaAgent retorna ao healthcheck normal)
5. Lock liberado — operações retomadas normalmente
```

O log é mantido em memória (não persiste em disco) — se o Gerente de BD reiniciar durante um failover, o BD recuperado entra como réplica a partir do snapshot atual do principal, sem replay. Esse comportamento é aceitável para o escopo acadêmico do projeto.

## 5. Failover e disponibilidade

O failover opera independentemente por shard. Se o BD principal do Shard B cair, os Shards A e C continuam operando normalmente.

### Cenário: BD principal de um shard cai

Do ponto de vista dos serviços: **nada muda**.

Internamente, o Gerente de BD:

```markdown
1. Detecta falha no Ping gRPC ao ReplicaAgent do BD principal do shard afetado
   (ou falha na escrita local, se o principal for o arquivo .db local)
2. Promove a Réplica 1 desse shard → novo principal
   (o ReplicaAgent da Réplica 1 passa a receber writes diretos; o arquivo .db local é substituído
    por uma cópia do snapshot da réplica promovida, trazida via ApplyWrite reverso ou snapshot gRPC)
3. Inicia log de writes pendentes em memória para replay futuro
4. Cria nova Réplica N automaticamente (snapshot do novo principal via ReplicaAgent)
   para manter qtd_max_replicas
5. Redireciona todas as operações do shard para o novo principal
6. Continua atendendo requisições — zero interrupção visível
```

### Cenário: BD principal retorna

```markdown
1. Ping gRPC detecta que o ReplicaAgent do BD original voltou a responder
2. Executa replay dos writes pendentes (ver seção 4.4) via ApplyWrite gRPC
3. BD original entra como réplica — Réplica 1 permanece como principal
4. Réplica N+1 criada durante o failover é descartada (ReplicaAgent encerrado)
5. Log de pendentes é limpo
```

### Cenário: Gateway cai

Com DNS round-robin e dois Gateways em instâncias separadas, a falha de um Gateway causa no máximo uma requisição perdida para o cliente cujo DNS resolveu para o Gateway caído. Na próxima tentativa (ou no próximo TTL), o DNS entregará o IP do Gateway saudável. Quando o Gateway volta, o registro DNS simplesmente volta a ser utilizado no round-robin.

> Para testes via terminal, os CLIs podem ser configurados com `--gateway-url http://api.scd-inventario.local` — o DNS do sistema operacional resolve automaticamente.

## 6. Tecnologias e linguagens

| Componente | Linguagem | Tecnologia principal |
| --- | --- | --- |
| API Gateway (×2) | Python | FastAPI + `httpx` |
| Serviço de inventário | Python | FastAPI + `grpcio` |
| Serviço de transações | Python | FastAPI + `grpcio` |
| Serviço de notificação | Python | FastAPI + `pika` (RabbitMQ) + `grpcio` |
| Agente de manutenção | Python | `pika` + `grpcio` |
| Gerente de BD | **Java** | gRPC (`grpc-java`) + SQLite (JDBC) + ShardRouter |
| ReplicaAgent (×N) | **Java** | gRPC (`grpc-java`) + SQLite (JDBC) |
| Mensageria | — | RabbitMQ |
| Clientes CLI — módulos HTTP | Python | stdlib apenas (`urllib`, `json`, `os`) |
| Clientes CLI — aplicações | Python | `argparse` + módulos HTTP locais |
| Scripts de demo/carga | Python | scripts simulados |
| Infraestrutura | — | AWS EC2 + DNS round-robin |

## 7. Clientes CLI

### 7.1 Organização em camadas

Os CLIs são divididos em duas camadas por separação de responsabilidades:

| Camada | Localização | Responsabilidade |
| --- | --- | --- |
| **Módulos HTTP** | `clients/http/` | Abstraem toda a comunicação com o Gateway: URLs, headers, serialização JSON e tratamento de erros de rede. Expõem uma função por endpoint; devolvem dicts Python; sem lógica de UI. |
| **Aplicações CLI** | `clients/` | Orquestram menus, prompts, formatação de saída e loop de polling. Toda comunicação com o Gateway passa pelos módulos HTTP — nenhum CLI faz HTTP diretamente. |

Os módulos HTTP usam apenas a stdlib do Python (`urllib`, `json`, `os`) — sem dependências externas, executáveis diretamente como smoke test sem nenhum `venv`.

### 7.2 Módulos HTTP (`clients/http/`)

Um módulo auxiliar compartilhado (`_http.py`) centraliza a montagem de cabeçalhos, serialização JSON e tratamento de erros HTTP/rede. Os demais módulos importam esse e expõem funções nomeadas por operação.

| Arquivo | Endpoints cobertos |
| --- | --- |
| `_http.py` | Função `_request` compartilhada; lê `GATEWAY_URL` via argumento repassado pelo CLI |
| `auth_http.py` | `POST /auth/register`, `POST /auth/login` |
| `inventory_http.py` | `/products` (CRUD), `/watchlist` (criar/listar/remover), `/flash-offers` (criar/listar) |
| `transaction_http.py` | `POST /orders`, `GET /orders`, `GET /orders/{id}` |
| `notification_http.py` | `GET /notifications`, `PATCH /notifications/read` |
| `admin_http.py` | `GET /admin/db-status`, `POST /admin/promote-replica`, `GET /health` |

### 7.3 Aplicações CLI (`clients/`)

| Arquivo | Papel | Módulos utilizados |
| --- | --- | --- |
| `buyer_cli.py` | CLI do comprador | `auth_http`, `inventory_http`, `transaction_http`, `notification_http` |
| `seller_cli.py` | CLI do vendedor | `auth_http`, `inventory_http`, `notification_http` |
| `admin_cli.py` | CLI do administrador | `auth_http`, `admin_http`, `notification_http` |

Cada CLI aceita `--gateway-url` e `--poll-interval` via `argparse`. O token é mantido em memória após o login. O polling de notificações corre em thread separada, chamando o módulo de notificações periodicamente sem interferir no menu principal.

### 7.4 Estrutura de arquivos

```dir
clients/
├── http/
│   ├── _http.py
│   ├── auth_http.py
│   ├── inventory_http.py
│   ├── transaction_http.py
│   ├── notification_http.py
│   └── admin_http.py
├── buyer_cli.py
├── seller_cli.py
└── admin_cli.py
```
