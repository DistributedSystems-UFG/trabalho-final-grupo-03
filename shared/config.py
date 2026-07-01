"""
shared/config.py

Carrega config.yaml do diretório de trabalho.
Se não existir, copia config.template.yaml da raiz do projeto.

Importado como primeiro passo de cada serviço Python:

    from shared.config import config

Acesso:
    config.gateway_host
    config.inventario_host
    config.inventario_port
    config.db_manager_host
    config.db_manager_port
    config.rabbitmq_host
    config.rabbitmq_port
    config.rabbitmq_user
    config.rabbitmq_password
    config.agente_intervalo
    config.dados_diretorio
    config.auth_token_admin
"""

import logging
import os
import shutil
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

# ── caminhos ──────────────────────────────────────────────────────────────────

_ROOT       = Path(__file__).resolve().parent.parent   # raiz do repositório
_CONFIG     = Path("config.yaml")                      # relativo ao cwd
_TEMPLATE   = _ROOT / "config.template.yaml"


# ── bootstrap ─────────────────────────────────────────────────────────────────

def _bootstrap() -> None:
    if _CONFIG.exists():
        return
    if not _TEMPLATE.exists():
        raise FileNotFoundError(
            f"config.template.yaml não encontrado em {_TEMPLATE}. "
            "Certifique-se de rodar os serviços a partir da raiz do repositório."
        )
    shutil.copy(_TEMPLATE, _CONFIG)
    log.warning("config.yaml não encontrado — criado a partir do template padrão")


# ── loader ────────────────────────────────────────────────────────────────────

def _load() -> dict:
    _bootstrap()
    with _CONFIG.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


# ── Config ────────────────────────────────────────────────────────────────────

class Config:
    """Acesso tipado às chaves do config.yaml."""

    def __init__(self, data: dict) -> None:
        self._data = data

    # ── gateway ───────────────────────────────────────────────────────────────

    @property
    def gateway_host(self) -> str:
        return self._get("servidor.host", "0.0.0.0")

    @property
    def gateway_port(self) -> int:
        return self._get("servidor.porta", 80)

    # ── serviços internos ─────────────────────────────────────────────────────

    @property
    def inventario_host(self) -> str:
        return self._get("servicos.inventario.host", "localhost")

    @property
    def inventario_port(self) -> int:
        return self._get("servicos.inventario.porta", 8002)

    @property
    def transacoes_host(self) -> str:
        return self._get("servicos.transacoes.host", "localhost")

    @property
    def transacoes_port(self) -> int:
        return self._get("servicos.transacoes.porta", 8003)

    @property
    def notificacao_host(self) -> str:
        return self._get("servicos.notificacao.host", "localhost")

    @property
    def notificacao_port(self) -> int:
        return self._get("servicos.notificacao.porta", 8004)

    # ── gerente de BD ─────────────────────────────────────────────────────────

    @property
    def db_manager_host(self) -> str:
        return self._get("gerente_bd.host", "localhost")

    @property
    def db_manager_port(self) -> int:
        return self._get("gerente_bd.porta", 50050)

    @property
    def db_manager_address(self) -> str:
        return f"{self.db_manager_host}:{self.db_manager_port}"

    @property
    def max_replicas(self) -> int:
        return self._get("gerente_bd.qtd_max_replicas", 2)

    @property
    def replicas_host(self) -> str:
        return self._get("gerente_bd.replicas_host", "localhost")

    @property
    def replicas_porta_base(self) -> int:
        return self._get("gerente_bd.replicas_porta_base", 50100)

    # ── RabbitMQ ──────────────────────────────────────────────────────────────

    @property
    def rabbitmq_host(self) -> str:
        return self._get("fila_mensagens.host", "localhost")

    @property
    def rabbitmq_port(self) -> int:
        return self._get("fila_mensagens.porta", 5672)

    @property
    def rabbitmq_user(self) -> str:
        return self._get("fila_mensagens.usuario", "guest")

    @property
    def rabbitmq_password(self) -> str:
        return self._get("fila_mensagens.senha", "guest")

    # ── agente de manutenção ──────────────────────────────────────────────────

    @property
    def agente_intervalo(self) -> int:
        return self._get("agente_manutencao.intervalo_segundos", 30)

    # ── dados ─────────────────────────────────────────────────────────────────

    @property
    def dados_diretorio(self) -> str:
        return self._get("dados.diretorio", "./data")

    # ── auth ──────────────────────────────────────────────────────────────────

    @property
    def auth_token_admin(self) -> str:
        return self._get("auth.token_admin", "admin-secret-token")

    # ── helper ────────────────────────────────────────────────────────────────

    def _get(self, dot_path: str, default=None):
        """Navega dot_path no dict aninhado; retorna default se ausente."""
        parts = dot_path.split(".")
        cur = self._data
        for part in parts:
            if not isinstance(cur, dict):
                return default
            cur = cur.get(part)
            if cur is None:
                return default
        return cur


# ── instância singleton ───────────────────────────────────────────────────────

config: Config = Config(_load())
