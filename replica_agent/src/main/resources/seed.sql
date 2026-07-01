-- =============================================================
--  SCD Inventory — seed.sql (cópia usada pelo ReplicaAgent)
--  Executado tanto pelo Gerente de BD (principal) quanto por cada
--  ReplicaAgent na primeira inicialização — garante que a ESTRUTURA
--  de tabelas exista antes de qualquer ApplyWrite via replicação.
--  Os DADOS (linhas) chegam via replicação assíncrona do principal,
--  não deste arquivo — o INSERT abaixo é idempotente (OR IGNORE) e
--  serve apenas de fallback caso a réplica nunca receba o write
--  original do admin.
-- =============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- -------------------------------------------------------------
--  GLOBAL — replicada nos 3 shards
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  username      TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK(role IN ('buyer','seller','admin')),
  token         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id),
  message    TEXT NOT NULL,
  read       INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

-- -------------------------------------------------------------
--  PARTICIONADAS por category do produto
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS products (
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

CREATE TABLE IF NOT EXISTS orders (
  id          TEXT PRIMARY KEY,
  buyer_id    TEXT NOT NULL REFERENCES users(id),
  seller_id   TEXT NOT NULL REFERENCES users(id),
  product_id  TEXT NOT NULL REFERENCES products(id),
  quantity    INTEGER NOT NULL,
  total_price REAL NOT NULL,
  status      TEXT NOT NULL CHECK(status IN ('pending','confirmed','cancelled')),
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist (
  id         TEXT PRIMARY KEY,
  buyer_id   TEXT NOT NULL REFERENCES users(id),
  product_id TEXT NOT NULL REFERENCES products(id),
  max_price  REAL NOT NULL,
  notified   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flash_offers (
  id             TEXT PRIMARY KEY,
  product_id     TEXT NOT NULL REFERENCES products(id),
  original_price REAL NOT NULL,
  promo_price    REAL NOT NULL,
  status         TEXT NOT NULL CHECK(status IN ('active','expired')),
  created_at     TEXT NOT NULL,
  expires_at     TEXT NOT NULL
);

-- -------------------------------------------------------------
--  Tabela auxiliar de idempotência (gerenciada pelo Gerente de BD)
-- -------------------------------------------------------------

CREATE TABLE IF NOT EXISTS processed_writes (
  origin_id  TEXT PRIMARY KEY,
  created_at TEXT NOT NULL
);

-- -------------------------------------------------------------
--  Admin padrão (token deve ser alterado em produção)
-- -------------------------------------------------------------

INSERT OR IGNORE INTO users (id, username, password_hash, role, token)
VALUES (
  'admin-00000000-0000-0000-0000-000000000000',
  'admin',
  -- SHA-256 de "admin" (placeholder — alterar antes de deploy)
  '8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918',
  'admin',
  'admin-secret-token'
);
