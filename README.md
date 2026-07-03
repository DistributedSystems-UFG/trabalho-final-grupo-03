# SCD Inventário — Sistema de Controle de Inventário

**Disciplina:** Software Concorrente e Distribuído — UFG/INF 2026.1
**Professor:** Fábio Moreira Costa
**Entrega:** 02/07/2026
**Grupo:** 3
**Integrantes:** Deivison Oliveira Da Silva, Leonardo Cortes Filho, Mateus De Almeida Souza, Matheus Geraldino De Melo

---

## 1. Como funciona

O sistema é um **marketplace** onde vendedores listam produtos com quantidade própria e compradores podem adquiri-los de duas formas:

- **Compra direta** — escolhendo um vendedor específico, via REST síncrono.
- **Compra inteligente** — watchlist de preço e ofertas relâmpago, via
  pub-sub assíncrono com RabbitMQ.

Um admin supervisiona o sistema via CLI privilegiado.

### Princípio central de dados

Para qualquer serviço do sistema, existe exatamente um banco de dados: o **Gerente de BD**. Nenhum outro componente conhece a existência de shards, banco principal, réplicas, ou quantos bancos estão rodando. Todo acesso a dados — leitura ou escrita — é feito via chamada gRPC ao Gerente de BD. A topologia interna, incluindo o particionamento por categoria, é detalhe de implementação exclusivo dele.

### Componentes

| Componente | Linguagem | Papel |
| --- | --- | --- |
| API Gateway (×2) | Python / FastAPI | Único ponto de entrada público, stateless |
| Serviço de Inventário | Python / FastAPI | Produtos, watchlist, ofertas relâmpago |
| Serviço de Transações | Python / FastAPI | Compra direta com reserva atômica de estoque |
| Serviço de Notificação | Python / FastAPI + pika | Consome eventos RabbitMQ, expõe polling HTTP |
| Agente de Manutenção | Python | Processo dedicado: alertas de estoque, watchlist, expiração de ofertas |
| Gerente de BD | Java / gRPC | Único componente autorizado a acessar bancos de dados |
| ReplicaAgent (×N) | Java / gRPC | Processo leve que persiste réplicas remotas, sem lógica de negócio |
| RabbitMQ | — | Message broker para os 4 eventos assíncronos do sistema |

### Modelos de interação

```markdown
Comprador / Vendedor / Admin
        │  HTTP (síncrono, bloqueante)
        ▼
   API Gateway (stateless, ×2 atrás de DNS round-robin)
        │  HTTP interno
        ▼
Inventário / Transações / Notificação
        │  gRPC (síncrono)              │  AMQP (assíncrono, pub-sub)
        ▼                                ▼
   Gerente de BD ──gRPC──► ReplicaAgents      RabbitMQ ◄── Agente de Manutenção
```

- **REST síncrono** — toda interação cliente → sistema.
- **gRPC síncrono** — todo acesso a dados, serviço → Gerente de BD, e
  Gerente de BD → ReplicaAgent (`ApplyWrite`/`Ping`).
- **Pub-sub assíncrono (RabbitMQ)** — os 4 eventos do sistema:
  `stock.low`, `order.completed`, `price.alert`, `flash.offer`.
- **Polling** — os CLIs consultam `GET /notifications` periodicamente
  (padrão 5s) em vez de manter conexão persistente.

### Particionamento e replicação

O banco é particionado em 3 shards por categoria de produto, cada um com réplicas remotas gerenciadas pelo Gerente de BD via gRPC leve:

| Shard | Categorias |
| --- | --- |
| shard_a | Eletrônicos, Informática, Telefonia |
| shard_b | Roupas, Calçados, Acessórios |
| shard_c | Casa, Esporte, Outros |

Tabelas globais (`users`, `notifications`) são replicadas nos 3 shards.
Tabelas particionadas (`products`, `orders`, `watchlist`, `flash_offers`) residem exclusivamente no shard da categoria do produto.

As portas dos `ReplicaAgent`s **não são digitadas pelo usuário** — são derivadas automaticamente a partir de `qtd_max_replicas` e `replicas_porta_base` no `config.yaml` (ver seção 3).

### Consistência e disponibilidade

- **Idempotência** via `origin_id` em toda escrita — evita duplicação em caso de retry de rede.
- **Locks** por `product_id` dentro de cada shard, serializando escritas concorrentes sem bloquear produtos distintos.
- **Failover automático**: o Gerente de BD detecta queda do principal de um shard via healthcheck periódico, promove a primeira réplica, e reaplica (replay) os writes pendentes quando o principal original volta.
- **Reads sempre vão ao principal** — réplicas existem exclusivamente para failover, nunca atendem leituras nem atrasam a confirmação de escrita ao cliente (replicação é fire-and-forget).

Mais detalhes de arquitetura, protobufs e estrutura de diretórios estão documentados em `DIVISAO_EQUIPE.md` (organização do código por responsável) e nos comentários dos próprios arquivos-fonte.

---

## 2. Checklist — requisitos da disciplina

### Objetivo da tarefa

> Exercitar, de forma integrada, os conceitos de sistemas distribuídos e programação concorrente na construção de um sistema de software, explorando métodos e padrões para solução dos principais problemas de concorrência e distribuição, com tecnologias e ferramentas de relevância atual.

### Características obrigatórias

| # | Requisito | Atendido | Como |
| --- | --- | --- | --- |
| 1 | Serviço acessível a múltiplos clientes na Internet | ✅ | Dois Gateways HTTP expostos publicamente via DNS round-robin (`api.scd-inventario.local`), deploy em AWS EC2 (ver seção "Implantação" abaixo) |
| 2 | Mais de uma linguagem / modelo de programação | ✅ | Python (FastAPI, asyncio, threading) para Gateway, serviços e CLIs; Java (gRPC, JDBC) para Gerente de BD e ReplicaAgents |
| 3 | Mais de um paradigma de interação (cliente-servidor, pub-sub, messaging) | ✅ | REST síncrono (clientes), gRPC síncrono (serviços ↔ dados), pub-sub assíncrono via RabbitMQ (eventos) |
| 4 | Componentes distribuídos integrados, implementados como parte do trabalho | ✅ | Gateway (×2), Inventário, Transações, Notificação, Agente de Manutenção, Gerente de BD, ReplicaAgents — nenhum é serviço de terceiros |
| 5 | Acessos concorrentes a recursos/dados compartilhados | ✅ | Lock por `product_id` no Gerente de BD; decremento atômico de estoque via `UPDATE ... WHERE quantity >= ?`; controle de ACK no RabbitMQ |
| 6 | Processamento no servidor concorrente com os acessos dos clientes | ✅ | Agente de Manutenção roda em processo separado, em loop contínuo, independente das requisições REST |
| 7 | Interação remota síncrona (bloqueante) | ✅ | REST cliente↔sistema; gRPC serviço↔Gerente de BD |
| 8 | Interação remota assíncrona | ✅ | RabbitMQ pub-sub (`stock.low`, `order.completed`, `price.alert`, `flash.offer`) |
| 9 | Replicação e particionamento de dados e funcionalidades | ✅ | 3 shards por categoria, cada um com N réplicas SQLite remotas (`qtd_max_replicas`, derivação automática de portas) |
| 10 | Consistência de dados e disponibilidade das funcionalidades | ✅ | Idempotência por `origin_id`; failover automático com promoção de réplica e replay de writes pendentes |

### Cenário de aplicação escolhido

Dentre os exemplos sugeridos no enunciado, o projeto implementa o **cenário 4 — Sistema de controle de inventário**: múltiplos vendedores e compradores realizam operações de saída (venda) e entrada (compra) de produtos simultaneamente, com alertas de baixa quantidade (`stock.low`) e operações internas de manutenção (expiração de ofertas relâmpago, reabilitação de alertas via flags `alerta_enviado`/`notified`).

Adicionalmente, o projeto estende o cenário com dois elementos não exigidos pelo enunciado mas que reforçam os requisitos de concorrência e distribuição: a **compra inteligente** (watchlist de preço com notificação assíncrona) e a **arquitetura de sharding com failover automático**, que vai além do CRUD simples mencionado no exemplo.

---

## 3. Como rodar

### Pré-requisitos

| Ferramenta | Versão mínima |
| --- | --- |
| Python | 3.11 |
| Java (JDK) | 17 |
| Maven | 3.9 |
| RabbitMQ | 3.12 |
| pip | 23 |

### 3.1 Instalar dependências

#### 3.1.1 Python e Pip

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
sudo update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11
pip install --upgrade pip
```

#### 3.1.2 Java e Maven

```bash
sudo apt install -y openjdk-17-jdk

MAVEN_VERSION=3.9.9
wget https://downloads.apache.org/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz
sudo tar -xzf apache-maven-${MAVEN_VERSION}-bin.tar.gz -C /opt
sudo ln -s /opt/apache-maven-${MAVEN_VERSION} /opt/maven

echo 'export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64' | sudo tee /etc/profile.d/maven.sh
echo 'export M2_HOME=/opt/maven' | sudo tee -a /etc/profile.d/maven.sh
echo 'export PATH=${M2_HOME}/bin:${PATH}' | sudo tee -a /etc/profile.d/maven.sh
source /etc/profile.d/maven.sh
```

#### 3.1.3 RabbitMQ

```bash
sudo apt install -y curl gnupg apt-transport-https

curl -1sLf 'https://dl.cloudsmith.io/public/rabbitmq/rabbitmq-erlang/setup.deb.sh' | sudo -E bash
curl -1sLf 'https://dl.cloudsmith.io/public/rabbitmq/rabbitmq-server/setup.deb.sh' | sudo -E bash

sudo apt update
sudo apt install -y erlang rabbitmq-server

sudo systemctl enable rabbitmq-server
sudo systemctl start rabbitmq-server
```

#### 3.1.4 Criando e usando o venv

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 3.1.5 Compilando os stubs grpc

```bash
# Gera stubs gRPC Python a partir dos .proto
python -m grpc_tools.protoc \
  -I proto \
  --python_out=. \
  --grpc_python_out=. \
  proto/dbmanager.proto \
  proto/replica.proto
```

### 3.2 Configuração inicial

O `config.yaml` **não é versionado**. Cada componente cria o seu automaticamente na primeira execução a partir do `config.template.yaml` (raiz, para Python) ou do template embutido no JAR (Java). Em desenvolvimento local, os valores padrão (`localhost`) já funcionam sem qualquer alteração.

Para configurar manualmente antes de subir (ex.: produção):

```bash
cp config.template.yaml config.yaml
# edite com os IPs privados das instâncias EC2
```

#### Topologia de réplicas — derivada automaticamente

O usuário **não escreve portas de réplica**. Basta declarar quantas
réplicas por shard são desejadas:

```yaml
gerente_bd:
  qtd_max_replicas: 2          # número de réplicas por shard
  replicas_host: "localhost"   # onde os ReplicaAgents rodam (EC2-6 em produção)
  replicas_porta_base: 50100   # porta base — portas derivadas a partir daqui
```

Gerente de BD e ReplicaAgents calculam, de forma independente mas idêntica, a porta de cada réplica pela fórmula:

```markdown
porta(shard, índice) = replicas_porta_base + (shard_index * 100) + índice
```

Com os valores padrão (`replicas_porta_base: 50100`, `qtd_max_replicas: 2`):

| Shard | Índice 0 | Índice 1 |
| --- | --- | --- |
| shard_a | 50100 | 50101 |
| shard_b | 50200 | 50201 |
| shard_c | 50300 | 50301 |

Para **aumentar o número de réplicas**: pare as instâncias, edite `qtd_max_replicas` e suba de novo —

```bash
bash scripts/stop_all_replicas.sh
# edite config.yaml: qtd_max_replicas: 3
bash scripts/start_all_replicas.sh
```

`start_all_replicas.sh` sempre sobe `0..qtd_max_replicas-1` para os 3 shards a partir do valor atual no `config.yaml`. Ele não verifica instâncias já em execução — rode `stop_all_replicas.sh` antes de mudar a contagem para evitar conflito de porta.

### 3.3 Build dos componentes Java

```bash
cd db_manager && mvn package -q && cd ..
# Gera: db_manager/target/db-manager-1.0.0.jar

cd replica_agent && mvn package -q && cd ..
# Gera: replica_agent/target/replica-agent-1.0.0.jar
```

Os scripts `start_db_manager.sh` e `start_replica_agent.sh` também compilam automaticamente se o JAR não existir — o passo manual acima é útil apenas para builds antecipados ou debugging.

### 3.4 Sincronização dos `.proto`

O diretório `proto/` raiz é a fonte de verdade. Após qualquer alteração na
interface gRPC:

```bash
cp proto/dbmanager.proto db_manager/proto/
cp proto/replica.proto   db_manager/proto/
cp proto/replica.proto   replica_agent/proto/
```

Depois faça rebuild dos componentes Java afetados.

### 3.5 Ordem de inicialização

```bash
# 1. ReplicaAgents — sobe todas as instâncias (3 shards × qtd_max_replicas)
#    automaticamente, lendo qtd_max_replicas do config.yaml
bash scripts/start_all_replicas.sh

# 2. Gerente de BD
bash scripts/start_db_manager.sh

# 3. RabbitMQ
sudo systemctl start rabbitmq-server

# 4. Inventário + Transações
bash scripts/start_inventario.sh --port 8002
bash scripts/start_transacoes.sh --port 8003

# 5. Notificação + Agente de Manutenção
bash scripts/start_notificacao.sh --port 8004
bash scripts/start_agente.sh

# 6. Gateway — último, depende de todos os serviços internos
bash scripts/start_gateway.sh --port 8080
```

> **Porta 80 vs 8080:** o `config.template.yaml` usa `8080` como padrão porque a porta 80 exige privilégio root no Linux. Em produção (EC2), rode com `sudo`, configure `setcap 'cap_net_bind_service=+ep' $(which python3)`, ou use um proxy/systemd com `CAP_NET_BIND_SERVICE` para expor a porta 80 externamente enquanto o uvicorn escuta em 8080.

### 3.6 Clientes CLI

#### Organização dos clientes CLI

Os CLIs são divididos em duas camadas por separação de responsabilidades: os módulos em `clients/http/` abstraem toda a comunicação HTTP com o Gateway — URLs, headers, serialização JSON e tratamento de erros de rede —, enquanto os CLIs (`buyer_cli.py`, `seller_cli.py`, `admin_cli.py`) concentram menus, prompts e polling, sem fazer HTTP diretamente.

Essa divisão é uma escolha de modularização: os módulos em `clients/http/` funcionam como interface programática local para o Gateway, isolando o restante do código de qualquer detalhe de transporte HTTP.

```bash
# Comprador
python clients/buyer_cli.py --gateway-url http://localhost:8080

# Vendedor
python clients/seller_cli.py --gateway-url http://localhost:8080

# Admin
python clients/admin_cli.py --gateway-url http://localhost:8080
```

Em produção, use o hostname DNS em vez do IP/porta locais:
`--gateway-url http://api.scd-inventario.local`.

O intervalo de polling de notificações é configurável via
`--poll-interval <segundos>` (padrão: 5).

Os módulos em `clients/http/` podem ser executados diretamente, **sem venv**, como smoke test de cada grupo de endpoints:

```bash
export GATEWAY_URL=http://localhost:8080
export GATEWAY_TOKEN=<token>
python clients/http/inventory_http.py
```

### 3.7 Acesso de outra máquina na mesma rede

O Gateway escuta em `0.0.0.0`, então aceita conexões de qualquer interface — não é necessário AWS para isso, apenas estar na mesma rede local:

```bash
# na máquina que roda o sistema:
hostname -I   # descobre o IP local, ex.: 192.168.1.50
sudo ufw allow 8080/tcp   # libera a porta no firewall, se houver um ativo

# em outro computador na mesma rede:
python clients/buyer_cli.py --gateway-url http://192.168.1.50:8080
```

Os demais serviços (`inventario`, `transacoes`, `notificacao`, `gerente_bd`) continuam em `localhost` e não são expostos — o Gateway é o único ponto de entrada, mesmo em acesso via LAN.

AWS só é necessário para expor o sistema na **internet pública** (IP fixo, DNS round-robin entre as duas instâncias de Gateway).

### 3.8 Demonstração e carga

```bash
# roteiro guiado, passo a passo, para apresentação
bash demo/scenario.sh http://localhost:8080

# script de carga concorrente (múltiplos compradores/vendedores simultâneos)
python demo/simulate_load.py --gateway http://localhost:8080 --users 5 --rounds 3
```

### 3.9 Endpoints principais

| Método | Rota | Descrição |
| --- | --- | --- |
| `POST` | `/auth/register` | Cadastro (sem auth) |
| `POST` | `/auth/login` | Login (sem auth) |
| `GET` | `/health` | Health check do Gateway |
| `GET/POST` | `/products` | Catálogo de produtos |
| `POST` | `/orders` | Compra direta |
| `POST/GET` | `/watchlist` | Watchlist de preço |
| `POST/GET` | `/flash-offers` | Ofertas relâmpago |
| `GET/PATCH` | `/notifications` | Notificações via polling |
| `GET` | `/admin/db-status` | Topologia de shards (admin) |
| `POST` | `/admin/promote-replica` | Promoção manual de réplica (admin) |

Header obrigatório em todas as rotas (exceto `/auth/*` e `/health`):

```markdown
X-Auth-Token: <token>
```
