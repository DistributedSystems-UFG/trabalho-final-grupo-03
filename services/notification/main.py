"""
services/notification/main.py

Serviço de Notificação — expõe endpoints HTTP para polling de notificações
e sobe o consumer RabbitMQ em thread daemon no startup.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from services.notification import db_client
from services.notification.consumer import start_consumer_thread


# ── lifespan — sobe consumer no startup ──────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_consumer_thread()
    yield


app = FastAPI(title="Serviço de Notificação", version="1.0.0", lifespan=lifespan)


# ── deps ──────────────────────────────────────────────────────────────────────

def current_user(
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
) -> dict:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return {"id": x_user_id, "role": x_user_role}


# ── schemas ───────────────────────────────────────────────────────────────────

class MarkReadBody(BaseModel):
    ids: list[str]


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.get("/notifications")
def list_notifications(
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    user = current_user(x_user_id, x_user_role)
    return db_client.list_notifications(user["id"])


@app.patch("/notifications/read")
def mark_read(
    body: MarkReadBody,
    x_user_id:   str = Header(default=""),
    x_user_role: str = Header(default=""),
):
    current_user(x_user_id, x_user_role)
    if not body.ids:
        raise HTTPException(status_code=400, detail="ids não pode ser vazio")
    db_client.mark_notifications_read(body.ids)
    return {"ok": True}
