"""
gateway/auth.py

Resolução de token e autorização por role.

O Gateway é stateless: a cada requisição, consulta o Gerente de BD
via gRPC para resolver token → user_id + role.
Nenhuma sessão ou cache local é mantido.
"""

import grpc
from fastapi import Header, HTTPException, Request

from shared.config import config

# Importa stubs gerados pelo grpc_tools.protoc
import dbmanager_pb2
import dbmanager_pb2_grpc

# ── canal gRPC (singleton por processo) ──────────────────────────────────────

_channel: grpc.Channel | None = None
_stub: dbmanager_pb2_grpc.DBManagerStub | None = None


def _get_stub() -> dbmanager_pb2_grpc.DBManagerStub:
    global _channel, _stub
    if _stub is None:
        _channel = grpc.insecure_channel(config.db_manager_address)
        _stub    = dbmanager_pb2_grpc.DBManagerStub(_channel)
    return _stub


# ── resolução de token ────────────────────────────────────────────────────────

def resolve_token(token: str) -> dict | None:
    """
    Consulta o Gerente de BD e retorna { id, role } para o token,
    ou None se não encontrado.
    """
    stub = _get_stub()
    req  = dbmanager_pb2.ReadRequest(
        category="global",
        sql="SELECT id, role FROM users WHERE token = ?",
        params=[token],
    )
    try:
        result = stub.Read(req)
    except grpc.RpcError as e:
        raise HTTPException(status_code=503, detail=f"Gerente de BD indisponível: {e.details()}")

    if not result.success or not result.rows:
        return None

    import json
    row = json.loads(result.rows[0])
    return {"id": row["id"], "role": row["role"]}


# ── dependências FastAPI ──────────────────────────────────────────────────────

def _extract_token(x_auth_token: str = Header(default="")) -> str:
    if not x_auth_token:
        raise HTTPException(status_code=401, detail="Header X-Auth-Token ausente")
    return x_auth_token


def get_current_user(x_auth_token: str = Header(default="")) -> dict:
    """
    Dependência FastAPI: resolve token e retorna { id, role }.
    Levanta 401 se token inválido.
    """
    if not x_auth_token:
        raise HTTPException(status_code=401, detail="Header X-Auth-Token ausente")

    user = resolve_token(x_auth_token)
    if user is None:
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    return user


def require_role(*roles: str):
    """
    Fábrica de dependência FastAPI que exige um dos roles informados.

    Uso:
        @router.get("/products", dependencies=[Depends(require_role("buyer", "seller"))])
    """
    def dependency(x_auth_token: str = Header(default="")) -> dict:
        user = get_current_user(x_auth_token)
        if user["role"] not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Acesso negado. Roles permitidos: {list(roles)}"
            )
        return user
    return dependency


# ── atalhos por role ──────────────────────────────────────────────────────────

def require_buyer(x_auth_token: str = Header(default="")) -> dict:
    return require_role("buyer")(x_auth_token)

def require_seller(x_auth_token: str = Header(default="")) -> dict:
    return require_role("seller")(x_auth_token)

def require_admin(x_auth_token: str = Header(default="")) -> dict:
    # Admin pode usar token estático do config ou ter entrada na tabela users
    if x_auth_token == config.auth_token_admin:
        return {"id": "admin", "role": "admin"}
    return require_role("admin")(x_auth_token)

def require_buyer_or_seller(x_auth_token: str = Header(default="")) -> dict:
    return require_role("buyer", "seller")(x_auth_token)
