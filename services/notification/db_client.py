"""
services/notification/db_client.py

Cliente gRPC para o Gerente de BD — Serviço de Notificação.
Persiste e consulta notificações (tabela global) e busca
compradores com interesse em produtos/categorias para flash offers.
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


def _write(category: str, sql: str, params: list[str], origin_id: str = "") -> None:
    if not origin_id:
        origin_id = str(uuid.uuid4())
    req = dbmanager_pb2.WriteRequest(
        category=category, sql=sql, params=params, origin_id=origin_id,
    )
    ack = _get_stub().Write(req)
    if not ack.success:
        raise RuntimeError(f"Write falhou: {ack.error}")


# ── notificações ──────────────────────────────────────────────────────────────

def create_notification(user_id: str, message: str) -> None:
    notif_id = str(uuid.uuid4())
    _write(
        "global",
        """INSERT INTO notifications (id, user_id, message, read, created_at)
           VALUES (?,?,?,0,datetime('now'))""",
        [notif_id, user_id, message],
        origin_id=f"notif-{notif_id}",
    )


def list_notifications(user_id: str) -> list[dict]:
    return _read(
        "global",
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC",
        [user_id],
    )


def mark_notifications_read(notif_ids: list[str]) -> None:
    if not notif_ids:
        return
    placeholders = ",".join("?" * len(notif_ids))
    _write(
        "global",
        f"UPDATE notifications SET read = 1 WHERE id IN ({placeholders})",
        notif_ids,
        origin_id=f"read-{notif_ids[0]}-batch",
    )


# ── watchlist (para flash offers) ─────────────────────────────────────────────

def get_buyers_watching(product_id: str, category: str) -> list[str]:
    """
    Retorna lista de buyer_ids com interesse no produto ou na categoria.
    Busca watchlist no shard correto da categoria do produto.
    """
    rows = _read(
        category,
        """SELECT DISTINCT w.buyer_id FROM watchlist w
           JOIN products p ON w.product_id = p.id
           WHERE w.product_id = ? OR p.category = ?""",
        [product_id, category],
    )
    return [row["buyer_id"] for row in rows]
