"""
gateway/main.py

Único ponto de entrada para todos os clientes externos.
Stateless: não armazena estado entre requisições.
Autentica via token consultando o Gerente de BD a cada request.
Roteia chamadas para os serviços internos via httpx.

Inicialização:
    bash scripts/start_gateway.sh --port 80
"""

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from gateway.auth import (
    get_current_user,
    require_admin,
    require_buyer,
    require_buyer_or_seller,
    require_seller,
)
from shared.config import config

app = FastAPI(title="SCD Inventário — API Gateway", version="1.0.0")

# ── clientes HTTP para os serviços internos ───────────────────────────────────

def _svc_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"

INVENTARIO  = _svc_url(config.inventario_host,  config.inventario_port)
TRANSACOES  = _svc_url(config.transacoes_host,  config.transacoes_port)
NOTIFICACAO = _svc_url(config.notificacao_host, config.notificacao_port)

_http = httpx.AsyncClient(timeout=30.0)


# ── proxy helper ──────────────────────────────────────────────────────────────

async def _proxy(request: Request, target_url: str, user: dict | None = None) -> Response:
    """
    Repassa a requisição para o serviço interno, injetando X-User-Id e X-User-Role.
    Preserva método, path params, query string e body.
    """
    headers = dict(request.headers)
    headers.pop("host", None)

    if user:
        headers["X-User-Id"]   = user["id"]
        headers["X-User-Role"] = user["role"]

    body = await request.body()

    try:
        resp = await _http.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Serviço indisponível: {e}")

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers=dict(resp.headers),
        media_type=resp.headers.get("content-type", "application/json"),
    )


# ── health check (sem auth) ───────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


# ── usuários (sem auth) ───────────────────────────────────────────────────────

@app.post("/users")
async def create_user(request: Request):
    return await _proxy(request, f"{INVENTARIO}/users")

@app.post("/auth/register")
async def register(request: Request):
    return await _proxy(request, f"{INVENTARIO}/users")

@app.post("/users/login")
async def login(request: Request):
    return await _proxy(request, f"{INVENTARIO}/users/login")

@app.post("/auth/login")
async def auth_login(request: Request):
    return await _proxy(request, f"{INVENTARIO}/users/login")


# ── produtos ──────────────────────────────────────────────────────────────────

@app.get("/products")
async def list_products(request: Request, user: dict = Depends(require_buyer_or_seller)):
    return await _proxy(request, f"{INVENTARIO}/products", user)


@app.get("/products/{product_id}")
async def get_product(product_id: str, request: Request,
                      user: dict = Depends(require_buyer_or_seller)):
    return await _proxy(request, f"{INVENTARIO}/products/{product_id}", user)


@app.post("/products")
async def create_product(request: Request, user: dict = Depends(require_seller)):
    return await _proxy(request, f"{INVENTARIO}/products", user)


@app.put("/products/{product_id}")
async def update_product(product_id: str, request: Request,
                         user: dict = Depends(require_seller)):
    return await _proxy(request, f"{INVENTARIO}/products/{product_id}", user)


@app.delete("/products/{product_id}")
async def delete_product(product_id: str, request: Request,
                         user: dict = Depends(require_seller)):
    return await _proxy(request, f"{INVENTARIO}/products/{product_id}", user)


# ── pedidos ───────────────────────────────────────────────────────────────────

@app.post("/orders")
async def create_order(request: Request, user: dict = Depends(require_buyer)):
    return await _proxy(request, f"{TRANSACOES}/orders", user)


@app.get("/orders")
async def list_orders(request: Request, user: dict = Depends(require_buyer_or_seller)):
    return await _proxy(request, f"{TRANSACOES}/orders", user)


# ── watchlist ─────────────────────────────────────────────────────────────────

@app.post("/watchlist")
async def create_watchlist(request: Request, user: dict = Depends(require_buyer)):
    return await _proxy(request, f"{INVENTARIO}/watchlist", user)


@app.get("/watchlist")
async def list_watchlist(request: Request, user: dict = Depends(require_buyer)):
    return await _proxy(request, f"{INVENTARIO}/watchlist", user)


@app.delete("/watchlist/{wl_id}")
async def delete_watchlist(wl_id: str, request: Request,
                           user: dict = Depends(require_buyer)):
    return await _proxy(request, f"{INVENTARIO}/watchlist/{wl_id}", user)


# ── ofertas relâmpago ─────────────────────────────────────────────────────────

@app.post("/flash-offers")
async def create_flash_offer(request: Request, user: dict = Depends(require_seller)):
    return await _proxy(request, f"{INVENTARIO}/flash-offers", user)


@app.get("/flash-offers")
async def list_flash_offers(request: Request,
                            user: dict = Depends(require_buyer_or_seller)):
    return await _proxy(request, f"{INVENTARIO}/flash-offers", user)


# ── notificações ──────────────────────────────────────────────────────────────

@app.get("/notifications")
async def list_notifications(request: Request,
                             user: dict = Depends(require_buyer_or_seller)):
    return await _proxy(request, f"{NOTIFICACAO}/notifications", user)


@app.patch("/notifications/read")
async def mark_notifications_read(request: Request,
                                  user: dict = Depends(require_buyer_or_seller)):
    return await _proxy(request, f"{NOTIFICACAO}/notifications/read", user)


# ── admin ─────────────────────────────────────────────────────────────────────

@app.get("/admin/status")
async def admin_status(request: Request, user: dict = Depends(require_admin)):
    return await _proxy(request, f"{INVENTARIO}/admin/status", user)

@app.get("/admin/db-status")
async def admin_db_status(request: Request, user: dict = Depends(require_admin)):
    return await _proxy(request, f"{INVENTARIO}/admin/status", user)

@app.post("/admin/promote")
async def admin_promote(request: Request, user: dict = Depends(require_admin)):
    return await _proxy(request, f"{INVENTARIO}/admin/promote", user)

@app.post("/admin/promote-replica")
async def admin_promote_replica(request: Request, user: dict = Depends(require_admin)):
    return await _proxy(request, f"{INVENTARIO}/admin/promote", user)


# ── shutdown ──────────────────────────────────────────────────────────────────

@app.on_event("shutdown")
async def shutdown():
    await _http.aclose()
