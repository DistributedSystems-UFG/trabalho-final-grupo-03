"""
services/agente/worker.py

Agente de Manutenção — processo Python dedicado, loop contínuo.
Sem porta HTTP. Roda independente dos servidores FastAPI.

Responsabilidades:
  1. Alerta de estoque baixo   → publica stock.low
  2. Watchlist de preço        → publica price.alert
  3. Expiração de flash offers → restaura preço original
"""

import json
import logging
import time
import uuid

import pika

import dbmanager_pb2
import dbmanager_pb2_grpc
import grpc
from shared.config import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agente] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# ── gRPC ──────────────────────────────────────────────────────────────────────

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
           product_id: str = "", origin_id: str = "") -> None:
    if not origin_id:
        origin_id = str(uuid.uuid4())
    req = dbmanager_pb2.WriteRequest(
        category=category, sql=sql, params=params,
        product_id=product_id, origin_id=origin_id,
    )
    ack = _get_stub().Write(req)
    if not ack.success:
        log.warning("Write recusado: %s", ack.error)


# ── RabbitMQ ──────────────────────────────────────────────────────────────────

_rmq_conn   = None
_rmq_channel = None


def _get_rmq_channel():
    global _rmq_conn, _rmq_channel
    try:
        if _rmq_conn and _rmq_conn.is_open:
            return _rmq_channel
    except Exception:
        pass

    _rmq_conn = pika.BlockingConnection(pika.ConnectionParameters(
        host=config.rabbitmq_host,
        port=config.rabbitmq_port,
        credentials=pika.PlainCredentials(config.rabbitmq_user, config.rabbitmq_password),
    ))
    _rmq_channel = _rmq_conn.channel()
    _rmq_channel.exchange_declare(exchange="events", exchange_type="topic", durable=True)
    return _rmq_channel


def _publish(routing_key: str, payload: dict) -> None:
    ch = _get_rmq_channel()
    ch.basic_publish(
        exchange="events",
        routing_key=routing_key,
        body=json.dumps(payload),
        properties=pika.BasicProperties(delivery_mode=2),
    )


# ── tarefa 1: alerta de estoque baixo ────────────────────────────────────────

def check_stock_alerts() -> None:
    """
    Para cada produto abaixo do limiar com alerta_enviado=0:
      → publica stock.low e marca alerta_enviado=1.
    Quando estoque volta acima, redefine alerta_enviado=0.
    """
    # Busca em todos os shards (category vazia)
    products = _read(
        "",
        "SELECT id, seller_id, name, category, quantity, alerta_quantidade, alerta_enviado "
        "FROM products",
        [],
    )

    for p in products:
        qty    = int(p["quantity"])
        limiar = int(p["alerta_quantidade"])
        enviado = int(p.get("alerta_enviado", 0))

        if qty < limiar and enviado == 0:
            _publish("stock.low", {
                "event":             "stock.low",
                "product_id":        p["id"],
                "product_name":      p["name"],
                "seller_id":         p["seller_id"],
                "quantity":          qty,
                "alerta_quantidade": limiar,
            })
            _write(
                p["category"],
                "UPDATE products SET alerta_enviado = 1 WHERE id = ?",
                [p["id"]],
                product_id=p["id"],
                origin_id=f"alerta-set-{p['id']}",
            )
            log.info("stock.low publicado: %s (qty=%d)", p["name"], qty)

        elif qty >= limiar and enviado == 1:
            # Estoque recuperado — reabilita alerta para próximo episódio
            _write(
                p["category"],
                "UPDATE products SET alerta_enviado = 0 WHERE id = ?",
                [p["id"]],
                product_id=p["id"],
                origin_id=f"alerta-reset-{p['id']}-{qty}",
            )
            log.debug("alerta_enviado resetado: %s (qty=%d)", p["name"], qty)


# ── tarefa 2: watchlist de preço ──────────────────────────────────────────────

def check_watchlist_alerts() -> None:
    """
    Para cada entrada watchlist com price <= max_price e notified=0:
      → publica price.alert e marca notified=1.
    Quando preço sobe acima, redefine notified=0.
    """
    entries = _read(
        "",
        """SELECT w.id, w.buyer_id, w.product_id, w.max_price, w.notified,
                  p.name AS product_name, p.price AS current_price, p.category
           FROM watchlist w
           JOIN products p ON w.product_id = p.id""",
        [],
    )

    for e in entries:
        current   = float(e["current_price"])
        max_price = float(e["max_price"])
        notified  = int(e.get("notified", 0))
        category  = e["category"]

        if current <= max_price and notified == 0:
            _publish("price.alert", {
                "event":        "price.alert",
                "watchlist_id": e["id"],
                "product_id":   e["product_id"],
                "product_name": e["product_name"],
                "buyer_id":     e["buyer_id"],
                "current_price": current,
                "max_price":    max_price,
            })
            _write(
                category,
                "UPDATE watchlist SET notified = 1 WHERE id = ?",
                [e["id"]],
                origin_id=f"wl-notified-{e['id']}",
            )
            log.info("price.alert publicado: %s (R$%.2f ≤ R$%.2f)",
                     e["product_name"], current, max_price)

        elif current > max_price and notified == 1:
            _write(
                category,
                "UPDATE watchlist SET notified = 0 WHERE id = ?",
                [e["id"]],
                origin_id=f"wl-reset-{e['id']}-{current:.2f}",
            )
            log.debug("watchlist notified resetado: %s (R$%.2f)",
                      e["product_name"], current)


# ── tarefa 3: expiração de flash offers ───────────────────────────────────────

def expire_flash_offers() -> None:
    """
    Para cada flash_offer com expires_at vencido e status='active':
      → atualiza status='expired' e restaura original_price no produto.
    """
    offers = _read(
        "",
        """SELECT f.id, f.product_id, f.original_price, p.category
           FROM flash_offers f
           JOIN products p ON f.product_id = p.id
           WHERE f.status = 'active' AND f.expires_at <= datetime('now')""",
        [],
    )

    for offer in offers:
        category = offer["category"]

        _write(
            category,
            "UPDATE flash_offers SET status = 'expired' WHERE id = ?",
            [offer["id"]],
            product_id=offer["product_id"],
            origin_id=f"expire-offer-{offer['id']}",
        )
        _write(
            category,
            "UPDATE products SET price = ? WHERE id = ?",
            [str(offer["original_price"]), offer["product_id"]],
            product_id=offer["product_id"],
            origin_id=f"restore-price-{offer['id']}",
        )
        log.info("Flash offer expirada: %s — preço restaurado para R$%.2f",
                 offer["id"][:8], float(offer["original_price"]))


# ── loop principal ────────────────────────────────────────────────────────────

def run() -> None:
    interval = config.agente_intervalo
    log.info("Agente de manutenção iniciado (intervalo=%ds)", interval)

    while True:
        try:
            check_stock_alerts()
        except Exception as e:
            log.error("Erro em check_stock_alerts: %s", e)

        try:
            check_watchlist_alerts()
        except Exception as e:
            log.error("Erro em check_watchlist_alerts: %s", e)

        try:
            expire_flash_offers()
        except Exception as e:
            log.error("Erro em expire_flash_offers: %s", e)

        time.sleep(interval)


if __name__ == "__main__":
    run()
