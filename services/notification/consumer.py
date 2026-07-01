"""
services/notification/consumer.py

Consumer RabbitMQ — consome todos os eventos do exchange 'events'
e persiste notificações via Gerente de BD.

Eventos consumidos:
  stock.low        → alerta para o vendedor
  order.completed  → notificação para comprador e vendedor
  price.alert      → notificação para o comprador da watchlist
  flash.offer      → notificação para compradores com interesse no produto/categoria

Iniciado em thread daemon pelo lifespan do FastAPI (main.py).
"""

import json
import logging
import threading

import pika

from services.notification import db_client
from shared.config import config

log = logging.getLogger(__name__)

# ── handlers por evento ───────────────────────────────────────────────────────

def _handle_stock_low(payload: dict) -> None:
    seller_id    = payload["seller_id"]
    product_name = payload["product_name"]
    quantity     = payload["quantity"]
    alerta       = payload["alerta_quantidade"]
    msg = (f"⚠ Estoque baixo: '{product_name}' com {quantity} unidade(s) "
           f"(alerta em {alerta}).")
    db_client.create_notification(seller_id, msg)
    log.info("stock.low → notificação criada para seller %s", seller_id)


def _handle_order_completed(payload: dict) -> None:
    product_name = payload["product_name"]
    quantity     = payload["quantity"]
    total        = payload["total_price"]

    db_client.create_notification(
        payload["buyer_id"],
        f"✔ Pedido confirmado: {quantity}x '{product_name}' por R${total:.2f}.",
    )
    db_client.create_notification(
        payload["seller_id"],
        f"💰 Venda realizada: {quantity}x '{product_name}' por R${total:.2f}.",
    )
    log.info("order.completed → notificações criadas para buyer e seller")


def _handle_price_alert(payload: dict) -> None:
    buyer_id     = payload["buyer_id"]
    product_name = payload["product_name"]
    current      = payload["current_price"]
    max_price    = payload["max_price"]
    msg = (f"🏷 Alerta de preço: '{product_name}' está a R${current:.2f} "
           f"(seu limite: R${max_price:.2f}).")
    db_client.create_notification(buyer_id, msg)
    log.info("price.alert → notificação criada para buyer %s", buyer_id)


def _handle_flash_offer(payload: dict) -> None:
    product_id   = payload["product_id"]
    product_name = payload["product_name"]
    category     = payload["category"]
    promo        = payload["promo_price"]
    expires      = payload["expires_at"]

    buyer_ids = db_client.get_buyers_watching(product_id, category)
    for buyer_id in buyer_ids:
        msg = (f"⚡ Oferta relâmpago: '{product_name}' por R${promo:.2f} "
               f"até {expires}.")
        db_client.create_notification(buyer_id, msg)

    log.info("flash.offer → notificações criadas para %d comprador(es)", len(buyer_ids))


HANDLERS = {
    "stock.low":       _handle_stock_low,
    "order.completed": _handle_order_completed,
    "price.alert":     _handle_price_alert,
    "flash.offer":     _handle_flash_offer,
}

# ── consumer ──────────────────────────────────────────────────────────────────

def _on_message(ch, method, properties, body):
    try:
        payload = json.loads(body)
        event   = payload.get("event", method.routing_key)
        handler = HANDLERS.get(event)
        if handler:
            handler(payload)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            log.warning("Evento desconhecido: %s — descartando", event)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except Exception as e:
        log.error("Erro ao processar evento %s: %s", method.routing_key, e)
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)


def _run_consumer() -> None:
    while True:
        try:
            conn = pika.BlockingConnection(pika.ConnectionParameters(
                host=config.rabbitmq_host,
                port=config.rabbitmq_port,
                credentials=pika.PlainCredentials(
                    config.rabbitmq_user, config.rabbitmq_password
                ),
                heartbeat=60,
            ))
            ch = conn.channel()
            ch.exchange_declare(exchange="events", exchange_type="topic", durable=True)

            # Fila exclusiva desta instância — recebe todos os eventos
            result = ch.queue_declare(queue="", exclusive=True)
            queue  = result.method.queue

            for routing_key in HANDLERS:
                ch.queue_bind(exchange="events", queue=queue, routing_key=routing_key)

            ch.basic_qos(prefetch_count=1)
            ch.basic_consume(queue=queue, on_message_callback=_on_message)

            log.info("Consumer RabbitMQ iniciado. Aguardando eventos...")
            ch.start_consuming()

        except pika.exceptions.AMQPConnectionError as e:
            log.warning("RabbitMQ desconectado (%s) — reconectando em 5s...", e)
            import time; time.sleep(5)
        except Exception as e:
            log.error("Erro inesperado no consumer: %s — reconectando em 5s...", e)
            import time; time.sleep(5)


def start_consumer_thread() -> threading.Thread:
    """Inicia o consumer em thread daemon. Chamado pelo lifespan do FastAPI."""
    t = threading.Thread(target=_run_consumer, name="rmq-consumer", daemon=True)
    t.start()
    return t
