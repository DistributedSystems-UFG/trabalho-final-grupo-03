"""
services/order/db_client.py

Cliente gRPC para o Gerente de BD — Serviço de Transações.
Lê category do produto, reserva estoque e persiste pedidos.
"""

import json
import uuid

import grpc

import dbmanager_pb2
import dbmanager_pb2_grpc
from shared.config import config

_channel: grpc.Channel | None = None
_stub: dbmanager_pb2_grpc.DBManagerStub | None = None


def _get_stub() -> dbmanager_pb2_grpc.DBManagerStub:
    global _channel, _stub
    if _stub is None:
        _channel = grpc.insecure_channel(config.db_manager_address)
        _stub    = dbmanager_pb2_grpc.DBManagerStub(_channel)
    return _stub


def _read(category: str, sql: str, params: list[str]) -> list[dict]:
    req    = dbmanager_pb2.ReadRequest(category=category, sql=sql, params=params)
    result = _get_stub().Read(req)
    if not result.success:
        raise RuntimeError(f"Read falhou: {result.error}")
    return [json.loads(row) for row in result.rows]


def _write(category: str, sql: str, params: list[str],
           product_id: str = "", origin_id: str = "") -> bool:
    """Retorna False se o Gerente de BD recusar (conflito de concorrência)."""
    if not origin_id:
        origin_id = str(uuid.uuid4())
    req = dbmanager_pb2.WriteRequest(
        category=category, sql=sql, params=params,
        product_id=product_id, origin_id=origin_id,
    )
    ack = _get_stub().Write(req)
    return ack.success


# ── produto ───────────────────────────────────────────────────────────────────

def get_product(product_id: str) -> dict | None:
    """Busca produto em todos os shards (category desconhecida pelo id)."""
    rows = _read("", "SELECT * FROM products WHERE id = ?", [product_id])
    return rows[0] if rows else None


def decrement_stock(product_id: str, category: str, quantity: int,
                    origin_id: str) -> bool:
    """
    Decrementa estoque atomicamente.
    A cláusula WHERE quantity >= ? garante que o write só é aplicado
    se houver estoque suficiente — o Gerente de BD retorna success=false
    se nenhuma linha for afetada (interpretado como conflito pelo serviço).
    """
    return _write(
        category,
        """UPDATE products SET quantity = quantity - ?
           WHERE id = ? AND quantity >= ?""",
        [str(quantity), product_id, str(quantity)],
        product_id=product_id,
        origin_id=origin_id,
    )


# ── pedidos ───────────────────────────────────────────────────────────────────

def create_order(order: dict, category: str) -> None:
    _write(
        category,
        """INSERT INTO orders
           (id, buyer_id, seller_id, product_id, quantity, total_price, status, created_at)
           VALUES (?,?,?,?,?,?,'confirmed',datetime('now'))""",
        [order["id"], order["buyer_id"], order["seller_id"],
         order["product_id"], str(order["quantity"]), str(order["total_price"])],
        product_id=order["product_id"],
        origin_id=f"order-{order['id']}",
    )


def list_orders_buyer(buyer_id: str) -> list[dict]:
    return _read("", "SELECT * FROM orders WHERE buyer_id = ?", [buyer_id])


def list_orders_seller(seller_id: str) -> list[dict]:
    return _read("", "SELECT * FROM orders WHERE seller_id = ?", [seller_id])
