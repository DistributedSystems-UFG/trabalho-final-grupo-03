"""
services/inventory/main.py

Serviço de Inventário — gerencia produtos, watchlist e flash offers.
Recebe X-User-Id / X-User-Role injetados pelo Gateway.
Todo acesso a dados via db_client (gRPC → Gerente de BD).
"""

import hashlib
import time
import uuid
from datetime import datetime, timedelta, timezone

import pika
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

import dbmanager_pb2
import dbmanager_pb2_grpc
import grpc
from services.inventory import db_client
from shared.config import config

app = FastAPI(title="Serviço de Inventário", version="1.0.0")

# ── RabbitMQ helper ───────────────────────────────────────────────────────────

def _get_rmq_channel():
    conn = pika.BlockingConnection(pika.ConnectionParameters(
        host=config.rabbitmq_host,
        port=config.rabbitmq_port,
        credentials=pika.PlainCredentials(config.rabbitmq_user, config.rabbitmq_password),
    ))
    ch = conn.channel()
    ch.exchange_declare(exchange="events", exchange_type="topic", durable=True)
    return conn, ch


def _publish(routing_key: str, payload: dict) -> None:
    import json
    conn, ch = _get_rmq_channel()
    try:
        ch.basic_publish(
            exchange="events",
            routing_key=routing_key,
            body=json.dumps(payload),
            properties=pika.BasicProperties(delivery_mode=2),
        )
    finally:
        conn.close()


# ── deps ──────────────────────────────────────────────────────────────────────

def current_user(
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
) -> dict:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return {"id": x_user_id, "role": x_user_role}


def require_role(user: dict, *roles: str) -> None:
    if user["role"] not in roles:
        raise HTTPException(status_code=403, detail="Acesso negado")


# ── schemas ───────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    password: str
    role:     str

class UserLogin(BaseModel):
    username: str
    password: str

class ProductCreate(BaseModel):
    name:              str
    description:       str = ""
    category:          str
    price:             float
    quantity:          int
    alerta_quantidade: int = 5

class ProductUpdate(BaseModel):
    price:             float
    quantity:          int
    alerta_quantidade: int

class WatchlistCreate(BaseModel):
    product_id: str
    max_price:  float

class FlashOfferCreate(BaseModel):
    product_id:       str
    discount_pct:     float
    duration_minutes: int


# ── usuários ──────────────────────────────────────────────────────────────────

@app.post("/users", status_code=201)
def create_user(body: UserCreate):
    if body.role not in ("buyer", "seller"):
        raise HTTPException(status_code=400, detail="role deve ser buyer ou seller")

    existing = db_client.get_user_by_username(body.username)
    if existing:
        raise HTTPException(status_code=409, detail="Username já existe")

    user_id       = str(uuid.uuid4())
    password_hash = hashlib.sha256(body.password.encode()).hexdigest()
    token         = hashlib.sha256(
        f"{body.username}{body.password}{time.time()}".encode()
    ).hexdigest()

    db_client.create_user(user_id, body.username, password_hash, body.role, token)
    return {"id": user_id, "token": token}


@app.post("/users/login")
def login(body: UserLogin):
    user = db_client.get_user_by_username(body.username)
    if not user:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    password_hash = hashlib.sha256(body.password.encode()).hexdigest()
    if user["password_hash"] != password_hash:
        raise HTTPException(status_code=401, detail="Credenciais inválidas")

    token = hashlib.sha256(
        f"{body.username}{body.password}{time.time()}".encode()
    ).hexdigest()
    db_client.update_user_token(user["id"], token)
    return {"id": user["id"], "token": token}


# ── produtos ──────────────────────────────────────────────────────────────────

@app.get("/products")
def list_products(
    category: str = "",
    name:     str = "",
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    current_user(x_user_id, x_user_role)
    return db_client.list_products(category=category, name=name)


@app.get("/products/{product_id}")
def get_product(
    product_id: str,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    current_user(x_user_id, x_user_role)
    product = db_client.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return product


@app.post("/products", status_code=201)
def create_product(
    body: ProductCreate,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    require_role(user, "seller")

    product_id = str(uuid.uuid4())
    product    = {
        "id":                product_id,
        "seller_id":         user["id"],
        "name":              body.name,
        "description":       body.description,
        "category":          body.category,
        "price":             body.price,
        "quantity":          body.quantity,
        "alerta_quantidade": body.alerta_quantidade,
    }
    db_client.create_product(product)
    return {"id": product_id}


@app.put("/products/{product_id}")
def update_product(
    product_id: str,
    body: ProductUpdate,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    require_role(user, "seller")

    product = db_client.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    if product["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Produto de outro vendedor")

    db_client.update_product(
        product_id, product["category"],
        body.price, body.quantity, body.alerta_quantidade,
    )
    return {"ok": True}


@app.delete("/products/{product_id}")
def delete_product(
    product_id: str,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    require_role(user, "seller")

    product = db_client.get_product(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    if product["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Produto de outro vendedor")

    db_client.delete_product(product_id, product["category"])
    return {"ok": True}


# ── watchlist ─────────────────────────────────────────────────────────────────

@app.post("/watchlist", status_code=201)
def create_watchlist(
    body: WatchlistCreate,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    require_role(user, "buyer")

    product = db_client.get_product(body.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    wl_id = str(uuid.uuid4())
    db_client.create_watchlist_entry(
        wl_id, user["id"], body.product_id, body.max_price, product["category"]
    )
    return {"id": wl_id}


@app.get("/watchlist")
def list_watchlist(
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    require_role(user, "buyer")
    return db_client.list_watchlist(user["id"])


@app.delete("/watchlist/{wl_id}")
def delete_watchlist(
    wl_id: str,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    require_role(user, "buyer")

    entry = db_client.get_watchlist_entry(wl_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Entrada não encontrada")
    if entry["buyer_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Entrada de outro comprador")

    product = db_client.get_product(entry["product_id"])
    category = product["category"] if product else ""
    db_client.delete_watchlist_entry(wl_id, category)
    return {"ok": True}


# ── flash offers ──────────────────────────────────────────────────────────────

@app.post("/flash-offers", status_code=201)
def create_flash_offer(
    body: FlashOfferCreate,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    require_role(user, "seller")

    product = db_client.get_product(body.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    if product["seller_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Produto de outro vendedor")

    original_price = float(product["price"])
    promo_price    = round(original_price * (1 - body.discount_pct / 100), 2)
    expires_at = (
        datetime.now(timezone.utc) + timedelta(minutes=body.duration_minutes)
    ).strftime("%Y-%m-%d %H:%M:%S")

    offer_id = str(uuid.uuid4())
    offer    = {
        "id":             offer_id,
        "product_id":     body.product_id,
        "category":       product["category"],
        "original_price": original_price,
        "promo_price":    promo_price,
        "expires_at":     expires_at,
    }

    db_client.create_flash_offer(offer)
    db_client.update_product_price(body.product_id, product["category"], promo_price)

    _publish("flash.offer", {
        "event":         "flash.offer",
        "flash_offer_id": offer_id,
        "product_id":    body.product_id,
        "product_name":  product["name"],
        "category":      product["category"],
        "seller_id":     user["id"],
        "original_price": original_price,
        "promo_price":   promo_price,
        "expires_at":    expires_at,
    })

    return {"id": offer_id, "promo_price": promo_price, "expires_at": expires_at}


@app.get("/flash-offers")
def list_flash_offers(
    category: str = "",
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    current_user(x_user_id, x_user_role)
    return db_client.list_flash_offers(category=category)


# ── admin (passa gRPC GetStatus / PromoteReplica) ─────────────────────────────

@app.get("/admin/status")
def admin_status(
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    require_role(user, "admin")

    import grpc
    import dbmanager_pb2, dbmanager_pb2_grpc
    channel = grpc.insecure_channel(config.db_manager_address)
    stub    = dbmanager_pb2_grpc.DBManagerStub(channel)
    resp    = stub.GetStatus(dbmanager_pb2.StatusRequest())
    return {
        "shards": [
            {
                "shard_id":       s.shard_id,
                "primary_id":     s.primary_id,
                "replica_ids":    list(s.replica_ids),
                "failover_active": s.failover_active,
            }
            for s in resp.shards
        ]
    }


class PromoteBody(BaseModel):
    shard_id:   str
    replica_id: str

@app.post("/admin/promote")
def admin_promote(
    body: PromoteBody,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    require_role(user, "admin")

    import grpc
    import dbmanager_pb2, dbmanager_pb2_grpc
    channel = grpc.insecure_channel(config.db_manager_address)
    stub    = dbmanager_pb2_grpc.DBManagerStub(channel)
    ack     = stub.PromoteReplica(dbmanager_pb2.PromoteRequest(
        shard_id=body.shard_id, replica_id=body.replica_id,
    ))
    if not ack.success:
        raise HTTPException(status_code=500, detail=ack.error)
    return {"ok": True, "new_primary": ack.new_primary}
