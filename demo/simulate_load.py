"""
demo/simulate_load.py

Simula carga concorrente no sistema: registra vendedores e compradores,
cadastra produtos em categorias distribuídas pelos 3 shards, dispara
compras simultâneas e verifica notificações.

Uso:
    python demo/simulate_load.py [--gateway http://localhost:8080] [--users 5] [--rounds 3]
"""

import argparse
import json
import random
import string
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx

# ── categorias por shard ──────────────────────────────────────────────────────

CATEGORIES = {
    "shard_a": ["Eletrônicos", "Informática", "Telefonia"],
    "shard_b": ["Roupas", "Calçados", "Acessórios"],
    "shard_c": ["Casa", "Esporte", "Outros"],
}
ALL_CATEGORIES = [c for cats in CATEGORIES.values() for c in cats]

# ── helpers ───────────────────────────────────────────────────────────────────

def rand_str(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=n))


def headers(token: str) -> dict:
    return {"X-Auth-Token": token}


def register(gw: str, role: str) -> dict:
    username = f"{role}_{rand_str()}"
    password = "senha123"
    r = httpx.post(f"{gw}/users", json={"username": username, "password": password, "role": role})
    r.raise_for_status()
    data = r.json()
    return {"id": data["id"], "token": data["token"], "username": username}


def create_product(gw: str, seller: dict, category: str) -> dict:
    payload = {
        "name": f"Produto {rand_str(5)}",
        "description": "Gerado por simulate_load",
        "category": category,
        "price": round(random.uniform(20, 500), 2),
        "quantity": random.randint(5, 50),
        "alerta_quantidade": 3,
    }
    r = httpx.post(f"{gw}/products", json=payload, headers=headers(seller["token"]))
    r.raise_for_status()
    return {**payload, "id": r.json()["id"], "seller_id": seller["id"]}


def buy(gw: str, buyer: dict, product_id: str, qty: int = 1) -> dict | None:
    r = httpx.post(
        f"{gw}/orders",
        json={"product_id": product_id, "quantity": qty},
        headers=headers(buyer["token"]),
    )
    if r.status_code == 409:
        return None   # conflito de estoque — esperado sob carga
    r.raise_for_status()
    return r.json()


def add_watchlist(gw: str, buyer: dict, product_id: str, max_price: float) -> None:
    httpx.post(
        f"{gw}/watchlist",
        json={"product_id": product_id, "max_price": max_price},
        headers=headers(buyer["token"]),
    ).raise_for_status()


def create_flash_offer(gw: str, seller: dict, product_id: str) -> dict:
    r = httpx.post(
        f"{gw}/flash-offers",
        json={"product_id": product_id, "discount_pct": random.uniform(5, 30), "duration_minutes": 2},
        headers=headers(seller["token"]),
    )
    r.raise_for_status()
    return r.json()


def poll_notifications(gw: str, user: dict) -> list:
    r = httpx.get(f"{gw}/notifications", headers=headers(user["token"]))
    r.raise_for_status()
    return r.json()


# ── cenários ──────────────────────────────────────────────────────────────────

def run_concurrent_purchases(gw: str, buyers: list, products: list, rounds: int) -> None:
    print(f"\n[CARGA] {len(buyers)} compradores × {rounds} rounds de compras simultâneas")

    def buyer_round(buyer: dict) -> dict:
        results = {"ok": 0, "conflict": 0, "error": 0}
        for _ in range(rounds):
            product = random.choice(products)
            try:
                order = buy(gw, buyer, product["id"], qty=1)
                if order is None:
                    results["conflict"] += 1
                else:
                    results["ok"] += 1
            except Exception as e:
                results["error"] += 1
                print(f"  [ERRO] {buyer['username']}: {e}")
        return results

    totals = {"ok": 0, "conflict": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=len(buyers)) as pool:
        futures = {pool.submit(buyer_round, b): b for b in buyers}
        for fut in as_completed(futures):
            res = fut.result()
            for k in totals:
                totals[k] += res[k]

    print(f"  ✔ confirmadas={totals['ok']}  conflito_estoque={totals['conflict']}  erros={totals['error']}")


def run_watchlist_scenario(gw: str, buyers: list, products: list) -> None:
    print("\n[WATCHLIST] Cadastrando entradas de watchlist...")
    for buyer in buyers:
        product = random.choice(products)
        # Define max_price acima do preço atual para garantir disparo imediato no agente
        max_price = round(product["price"] * 1.2, 2)
        add_watchlist(gw, buyer, product["id"], max_price)
        print(f"  {buyer['username']} → produto {product['id'][:8]}... max_price={max_price}")


def run_flash_offer_scenario(gw: str, sellers: list, products: list) -> None:
    print("\n[FLASH OFFER] Criando ofertas relâmpago...")
    for seller in sellers:
        seller_products = [p for p in products if p["seller_id"] == seller["id"]]
        if not seller_products:
            continue
        product = random.choice(seller_products)
        offer = create_flash_offer(gw, seller, product["id"])
        print(f"  {seller['username']} → produto {product['id'][:8]}... promo_price={offer['promo_price']}")


def check_notifications(gw: str, users: list) -> None:
    print("\n[NOTIFICAÇÕES] Verificando notificações recebidas...")
    for user in users:
        notifs = poll_notifications(gw, user)
        print(f"  {user['username']}: {len(notifs)} notificação(ões)")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Simulação de carga — SCD Inventário")
    parser.add_argument("--gateway", default="http://localhost:8080", help="URL base do Gateway")
    parser.add_argument("--users",  type=int, default=5,  help="Número de vendedores e compradores cada")
    parser.add_argument("--rounds", type=int, default=3,  help="Rounds de compra por comprador")
    args = parser.parse_args()

    gw = args.gateway.rstrip("/")
    print(f"Gateway: {gw}")
    print(f"Usuários por papel: {args.users} | Rounds: {args.rounds}\n")

    # ── health check ──────────────────────────────────────────────────────────
    r = httpx.get(f"{gw}/health")
    r.raise_for_status()
    print(f"[HEALTH] {r.json()}")

    # ── registro de usuários ──────────────────────────────────────────────────
    print(f"\n[SETUP] Registrando {args.users} vendedores e {args.users} compradores...")
    sellers = [register(gw, "seller") for _ in range(args.users)]
    buyers  = [register(gw, "buyer")  for _ in range(args.users)]
    print(f"  Vendedores: {[s['username'] for s in sellers]}")
    print(f"  Compradores: {[b['username'] for b in buyers]}")

    # ── cadastro de produtos (1 por categoria por vendedor) ───────────────────
    print(f"\n[SETUP] Cadastrando produtos em todas as categorias...")
    products = []
    for seller in sellers:
        category = random.choice(ALL_CATEGORIES)
        product = create_product(gw, seller, category)
        products.append(product)
        print(f"  {seller['username']} → {product['name']} [{category}] R${product['price']}")

    # ── cenários ──────────────────────────────────────────────────────────────
    run_concurrent_purchases(gw, buyers, products, args.rounds)
    run_watchlist_scenario(gw, buyers, products)
    run_flash_offer_scenario(gw, sellers, products)

    # Aguarda o agente de manutenção processar
    print("\n[AGUARDA] Esperando 5s para o agente processar eventos...")
    time.sleep(5)

    check_notifications(gw, buyers + sellers)

    print("\n[FIM] Simulação concluída.")


if __name__ == "__main__":
    main()
