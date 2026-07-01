"""
services/inventory/db_client.py

Cliente gRPC para o Gerente de BD.
Todos os acessos a dados do serviço de inventário passam por aqui.
Nenhuma conexão direta com SQLite — apenas stubs gRPC.
"""

import json
import uuid
from typing import Any

import grpc

import dbmanager_pb2
import dbmanager_pb2_grpc
from shared.config import config

# ── canal singleton ───────────────────────────────────────────────────────────

_channel: grpc.Channel | None = None
_stub: dbmanager_pb2_grpc.DBManagerStub | None = None


def _get_stub() -> dbmanager_pb2_grpc.DBManagerStub:
    global _channel, _stub
    if _stub is None:
        _channel = grpc.insecure_channel(config.db_manager_address)
        _stub    = dbmanager_pb2_grpc.DBManagerStub(_channel)
    return _stub


# ── helpers internos ──────────────────────────────────────────────────────────

def _read(category: str, sql: str, params: list[str]) -> list[dict]:
    req    = dbmanager_pb2.ReadRequest(category=category, sql=sql, params=params)
    result = _get_stub().Read(req)
    if not result.success:
        raise RuntimeError(f"Read falhou: {result.error}")
    return [json.loads(row) for row in result.rows]


def _write(category: str, sql: str, params: list[str],
           product_id: str = "", origin_id: str = "") -> None:
    if not origin_id:
        origin_id = str(uuid.uuid4())
    req = dbmanager_pb2.WriteRequest(
        category=category, sql=sql, params=params,
        product_id=product_id, origin_id=origin_id,
    )
    ack = _get_stub().Write(req)
    if not ack.success:
        raise RuntimeError(f"Write falhou: {ack.error}")


# ── usuários ──────────────────────────────────────────────────────────────────

def get_user_by_token(token: str) -> dict | None:
    rows = _read("global", "SELECT id, role FROM users WHERE token = ?", [token])
    return rows[0] if rows else None


def get_user_by_username(username: str) -> dict | None:
    rows = _read("global", "SELECT id, username, password_hash, role, token FROM users WHERE username = ?", [username])
    return rows[0] if rows else None


def create_user(user_id: str, username: str, password_hash: str, role: str, token: str) -> None:
    _write(
        "global",
        "INSERT INTO users (id, username, password_hash, role, token) VALUES (?,?,?,?,?)",
        [user_id, username, password_hash, role, token],
    )


def update_user_token(user_id: str, token: str) -> None:
    _write(
        "global",
        "UPDATE users SET token = ? WHERE id = ?",
        [token, user_id],
        origin_id=f"token-update-{user_id}-{token[:8]}",
    )


# ── produtos ──────────────────────────────────────────────────────────────────

def list_products(category: str = "", name: str = "") -> list[dict]:
    """Lista produtos. Se category vazia, consulta todos os shards."""
    if category and name:
        return _read(category,
                     "SELECT * FROM products WHERE category = ? AND name LIKE ?",
                     [category, f"%{name}%"])
    if category:
        return _read(category,
                     "SELECT * FROM products WHERE category = ?",
                     [category])
    if name:
        return _read("",
                     "SELECT * FROM products WHERE name LIKE ?",
                     [f"%{name}%"])
    return _read("", "SELECT * FROM products", [])


def get_product(product_id: str) -> dict | None:
    # Busca em todos os shards pois não sabemos a category pelo id
    rows = _read("", "SELECT * FROM products WHERE id = ?", [product_id])
    return rows[0] if rows else None


def create_product(product: dict) -> None:
    _write(
        product["category"],
        """INSERT INTO products
           (id, seller_id, name, description, category, price, quantity,
            alerta_quantidade, alerta_enviado, created_at)
           VALUES (?,?,?,?,?,?,?,?,0,datetime('now'))""",
        [product["id"], product["seller_id"], product["name"],
         product.get("description", ""), product["category"],
         str(product["price"]), str(product["quantity"]),
         str(product["alerta_quantidade"])],
        product_id=product["id"],
    )


def update_product(product_id: str, category: str,
                   price: float, quantity: int, alerta_quantidade: int) -> None:
    _write(
        category,
        "UPDATE products SET price=?, quantity=?, alerta_quantidade=? WHERE id=?",
        [str(price), str(quantity), str(alerta_quantidade), product_id],
        product_id=product_id,
        origin_id=f"upd-prod-{product_id}",
    )


def delete_product(product_id: str, category: str) -> None:
    _write(
        category,
        "DELETE FROM products WHERE id = ?",
        [product_id],
        product_id=product_id,
        origin_id=f"del-prod-{product_id}",
    )


# ── watchlist ─────────────────────────────────────────────────────────────────

def list_watchlist(buyer_id: str) -> list[dict]:
    return _read("", "SELECT * FROM watchlist WHERE buyer_id = ?", [buyer_id])


def get_watchlist_entry(wl_id: str) -> dict | None:
    rows = _read("", "SELECT * FROM watchlist WHERE id = ?", [wl_id])
    return rows[0] if rows else None


def create_watchlist_entry(wl_id: str, buyer_id: str,
                           product_id: str, max_price: float, category: str) -> None:
    _write(
        category,
        """INSERT INTO watchlist (id, buyer_id, product_id, max_price, notified, created_at)
           VALUES (?,?,?,?,0,datetime('now'))""",
        [wl_id, buyer_id, product_id, str(max_price)],
    )


def delete_watchlist_entry(wl_id: str, category: str) -> None:
    _write(category, "DELETE FROM watchlist WHERE id = ?", [wl_id],
           origin_id=f"del-wl-{wl_id}")


# ── flash offers ──────────────────────────────────────────────────────────────

def list_flash_offers(category: str = "") -> list[dict]:
    if category:
        return _read(category,
                     "SELECT f.* FROM flash_offers f JOIN products p ON f.product_id = p.id "
                     "WHERE p.category = ? AND f.status = 'active'",
                     [category])
    return _read("",
                 "SELECT * FROM flash_offers WHERE status = 'active'", [])


def create_flash_offer(offer: dict) -> None:
    _write(
        offer["category"],
        """INSERT INTO flash_offers
           (id, product_id, original_price, promo_price, status, created_at, expires_at)
           VALUES (?,?,?,?,'active',datetime('now'),?)""",
        [offer["id"], offer["product_id"],
         str(offer["original_price"]), str(offer["promo_price"]),
         offer["expires_at"]],
        product_id=offer["product_id"],
    )


def update_product_price(product_id: str, category: str, new_price: float) -> None:
    _write(
        category,
        "UPDATE products SET price = ? WHERE id = ?",
        [str(new_price), product_id],
        product_id=product_id,
        origin_id=f"price-upd-{product_id}-{new_price}",
    )
