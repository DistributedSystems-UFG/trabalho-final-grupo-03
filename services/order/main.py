"""
services/order/main.py

Serviço de Transações — processa compras diretas.
Fluxo de compra:
  1. Lê product (category + seller_id + price) via Gerente de BD
  2. Decrementa estoque atomicamente (WriteRequest com product_id lock)
  3. Se conflito (estoque insuficiente) → 409
  4. Persiste pedido no mesmo shard
  5. Publica order.completed no RabbitMQ
"""

import uuid

import pika
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from services.order import db_client
from shared.config import config

app = FastAPI(title="Serviço de Transações", version="1.0.0")

# ── RabbitMQ ──────────────────────────────────────────────────────────────────

def _publish(payload: dict) -> None:
    import json
    conn = pika.BlockingConnection(pika.ConnectionParameters(
        host=config.rabbitmq_host,
        port=config.rabbitmq_port,
        credentials=pika.PlainCredentials(config.rabbitmq_user, config.rabbitmq_password),
    ))
    ch = conn.channel()
    ch.exchange_declare(exchange="events", exchange_type="topic", durable=True)
    ch.basic_publish(
        exchange="events",
        routing_key="order.completed",
        body=json.dumps(payload),
        properties=pika.BasicProperties(delivery_mode=2),
    )
    conn.close()


# ── deps ──────────────────────────────────────────────────────────────────────

def current_user(
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
) -> dict:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return {"id": x_user_id, "role": x_user_role}


# ── schemas ───────────────────────────────────────────────────────────────────

class OrderCreate(BaseModel):
    product_id: str
    quantity:   int


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.post("/orders", status_code=201)
def create_order(
    body: OrderCreate,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    if user["role"] != "buyer":
        raise HTTPException(status_code=403, detail="Apenas compradores podem comprar")
    if body.quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantidade deve ser maior que zero")

    # 1. Lê produto — obtém category, seller_id e price
    product = db_client.get_product(body.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    if int(product["quantity"]) < body.quantity:
        raise HTTPException(status_code=409, detail="Estoque insuficiente")

    order_id    = str(uuid.uuid4())
    total_price = float(product["price"]) * body.quantity
    category    = product["category"]

    # 2. Decrementa estoque atomicamente — origin_id garante idempotência
    decrement_origin = f"decr-{order_id}"
    ok = db_client.decrement_stock(
        body.product_id, category, body.quantity, decrement_origin
    )
    if not ok:
        raise HTTPException(status_code=409,
                            detail="Estoque insuficiente (conflito de concorrência)")

    # 3. Persiste pedido no mesmo shard
    order = {
        "id":          order_id,
        "buyer_id":    user["id"],
        "seller_id":   product["seller_id"],
        "product_id":  body.product_id,
        "quantity":    body.quantity,
        "total_price": total_price,
    }
    db_client.create_order(order, category)

    # 4. Publica evento assíncrono
    try:
        _publish({
            "event":        "order.completed",
            "order_id":     order_id,
            "product_id":   body.product_id,
            "product_name": product["name"],
            "buyer_id":     user["id"],
            "seller_id":    product["seller_id"],
            "quantity":     body.quantity,
            "total_price":  total_price,
        })
    except Exception:
        pass  # publicação falhou mas pedido já foi confirmado — não reverte

    return {"id": order_id, "total_price": total_price, "status": "confirmed"}


@app.get("/orders")
def list_orders(
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    if user["role"] == "buyer":
        return db_client.list_orders_buyer(user["id"])
    if user["role"] == "seller":
        return db_client.list_orders_seller(user["id"])
    raise HTTPException(status_code=403, detail="Acesso negado")
