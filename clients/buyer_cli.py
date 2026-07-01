# clients/buyer_cli.py
import argparse, os, sys, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
import auth_http, inventory_http, transaction_http, notification_http

# ── helpers de UI ─────────────────────────────────────────────────────────────

def hr():       print("─" * 52)
def ok(msg):    print(f"  ✔ {msg}")
def err(msg):   print(f"  ✘ {msg}", file=sys.stderr)
def title(msg): print(f"\n{'─'*52}\n  {msg}\n{'─'*52}")

CATEGORIES = ["Eletrônicos", "Informática", "Telefonia",
              "Roupas", "Calçados", "Acessórios",
              "Casa", "Esporte", "Outros"]

# ── polling de notificações ───────────────────────────────────────────────────

def poll_notifications(token: str, interval: int, stop: threading.Event):
    while not stop.is_set():
        result = notification_http.get_notifications(token)
        notifs = result if isinstance(result, list) else []
        if notifs:
            print(f"\n\n  🔔 {len(notifs)} nova(s) notificação(ões):")
            for n in notifs:
                print(f"     • {n['message']}")
            notification_http.mark_read(token, [n["id"] for n in notifs])
            print("> ", end="", flush=True)
        stop.wait(interval)

# ── resolução de produto por nome ─────────────────────────────────────────────

def find_product(token: str, query: str) -> dict | None:
    matches = inventory_http.list_products(token, name=query)
    if not isinstance(matches, list) or not matches:
        err(f"Nenhum produto encontrado para '{query}'.")
        return None
    if len(matches) == 1:
        return matches[0]
    print(f"\n  {len(matches)} resultado(s):")
    for i, p in enumerate(matches, 1):
        print(f"    {i}. {p['name']:<24} R${p['price']:>8.2f}  qty={p['quantity']}  [{p['category']}]")
    choice = input("  Escolha o número (Enter para cancelar): ").strip()
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(matches):
            return matches[idx]
    except ValueError:
        pass
    return None

def prompt_product(token: str) -> dict | None:
    name = input("  Nome do produto: ").strip()
    if not name:
        err("Nome não pode ser vazio.")
        return None
    return find_product(token, name)

# ── ações ─────────────────────────────────────────────────────────────────────

def do_list_products(token: str):
    cat = input("  Categoria (Enter para todas): ").strip() or None
    name = input("  Nome (Enter para todos): ").strip() or None
    result = inventory_http.list_products(token, category=cat, name=name)
    items = result if isinstance(result, list) else []
    if not items:
        print("  Nenhum produto encontrado.")
        return
    print(f"\n  {len(items)} produto(s):")
    for p in items:
        print(f"  {p['name']:<24} R${p['price']:>8.2f}  qty={p['quantity']}  [{p['category']}]")

def do_buy(token: str):
    product = prompt_product(token)
    if not product:
        return
    print(f"  Produto: {product['name']}  R${product['price']:.2f}  (estoque: {product['quantity']})")
    qty_str = input("  Quantidade: ").strip()
    try:
        qty = int(qty_str)
    except ValueError:
        err("Quantidade inválida.")
        return
    result = transaction_http.buy(token, product["id"], qty)
    if "error" in result:
        code = result.get("status", "?")
        if code == 409:
            err("Estoque insuficiente (conflito). Tente outra quantidade.")
        else:
            err(f"Erro {code}: {result['error']}")
    else:
        ok(f"Pedido confirmado! '{product['name']}' x{qty} — total=R${result['total_price']:.2f}")

def do_my_orders(token: str):
    result = transaction_http.list_orders(token)
    orders = result if isinstance(result, list) else []
    if not orders:
        print("  Nenhum pedido encontrado.")
        return
    print(f"\n  {len(orders)} pedido(s):")
    for o in orders:
        prod = inventory_http.get_product(token, o["product_id"])
        name = prod.get("name", o["product_id"][:8] + "...") if isinstance(prod, dict) and "name" in prod else o["product_id"][:8] + "..."
        print(f"  {name:<24} qty={o['quantity']}  total=R${o['total_price']:.2f}  status={o['status']}  {o['created_at']}")

def do_watchlist_add(token: str):
    product = prompt_product(token)
    if not product:
        return
    print(f"  Produto: {product['name']}  (preço atual: R${product['price']:.2f})")
    max_price = input("  Preço máximo (R$): ").strip()
    try:
        result = inventory_http.add_watchlist(token, product["id"], float(max_price))
        if "error" in result:
            err(f"Erro: {result['error']}")
        else:
            ok(f"Watchlist criada para '{product['name']}'.")
    except ValueError:
        err("Preço inválido.")

def do_watchlist_list(token: str):
    result = inventory_http.list_watchlist(token)
    items = result if isinstance(result, list) else []
    if not items:
        print("  Watchlist vazia.")
        return
    print(f"\n  {len(items)} entrada(s):")
    for w in items:
        prod = inventory_http.get_product(token, w["product_id"])
        name = prod.get("name", w["product_id"][:8]) if isinstance(prod, dict) and "name" in prod else w["product_id"][:8]
        notified = "✔ notificado" if w.get("notified") else "aguardando"
        print(f"  {name:<24} max=R${w['max_price']:.2f}  {notified}")

def do_watchlist_remove(token: str):
    result = inventory_http.list_watchlist(token)
    items = result if isinstance(result, list) else []
    if not items:
        print("  Watchlist vazia.")
        return
    enriched = []
    for w in items:
        prod = inventory_http.get_product(token, w["product_id"])
        w["product_name"] = prod.get("name", w["product_id"][:8]) if isinstance(prod, dict) and "name" in prod else w["product_id"][:8]
        enriched.append(w)
    print("\n  Sua watchlist:")
    for i, w in enumerate(enriched, 1):
        print(f"    {i}. {w['product_name']:<24} max=R${w['max_price']:.2f}")
    choice = input("  Número a remover (Enter para cancelar): ").strip()
    try:
        idx = int(choice) - 1
        if not (0 <= idx < len(enriched)):
            err("Escolha inválida.")
            return
    except ValueError:
        return
    entry = enriched[idx]
    r = inventory_http.remove_watchlist(token, entry["id"])
    if "error" in r:
        err(f"Erro: {r['error']}")
    else:
        ok(f"'{entry['product_name']}' removido da watchlist.")

def do_flash_offers(token: str):
    cat = input("  Categoria (Enter para todas): ").strip() or None
    result = inventory_http.list_flash_offers(token, category=cat)
    offers = result if isinstance(result, list) else []
    if not offers:
        print("  Nenhuma oferta relâmpago ativa.")
        return
    print(f"\n  {len(offers)} oferta(s):")
    for f in offers:
        prod = inventory_http.get_product(token, f["product_id"])
        name = prod.get("name", f["product_id"][:8]) if isinstance(prod, dict) and "name" in prod else f["product_id"][:8]
        print(f"  {name:<24} promo=R${f['promo_price']:.2f}  expira={f['expires_at']}")

def do_notifications(token: str):
    result = notification_http.get_notifications(token)
    notifs = result if isinstance(result, list) else []
    if not notifs:
        print("  Sem notificações.")
        return
    print(f"\n  {len(notifs)} notificação(ões):")
    for n in notifs:
        print(f"  {n['message']}  ({n['created_at']})")
    notification_http.mark_read(token, [n["id"] for n in notifs])
    ok(f"{len(notifs)} marcada(s) como lida(s).")

# ── menu ──────────────────────────────────────────────────────────────────────

MENU = """
  1. Listar produtos
  2. Comprar produto
  3. Meus pedidos
  4. Watchlist — adicionar
  5. Watchlist — listar
  6. Watchlist — remover
  7. Ofertas relâmpago
  8. Notificações
  0. Sair
"""

ACTIONS = {
    "1": do_list_products,
    "2": do_buy,
    "3": do_my_orders,
    "4": do_watchlist_add,
    "5": do_watchlist_list,
    "6": do_watchlist_remove,
    "7": do_flash_offers,
    "8": do_notifications,
}

# ── autenticação ──────────────────────────────────────────────────────────────

def authenticate() -> str:
    title("SCD Inventário — Comprador")
    print("  1. Login\n  2. Cadastro")
    choice = input("\n> ").strip()
    username = input("  Usuário: ").strip()
    password = input("  Senha:   ").strip()
    if choice == "2":
        result = auth_http.register(username, password, "buyer")
    else:
        result = auth_http.login(username, password)
    if "error" in result:
        err(f"Falha: {result['error']}")
        sys.exit(1)
    ok(f"Bem-vindo! id={result['id'][:8]}...")
    return result["token"]

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CLI do Comprador — SCD Inventário")
    parser.add_argument("--gateway-url", default="http://localhost:8080")
    parser.add_argument("--poll-interval", type=int, default=5)
    args = parser.parse_args()
    os.environ["GATEWAY_URL"] = args.gateway_url

    token = authenticate()

    stop = threading.Event()
    t = threading.Thread(target=poll_notifications, args=(token, args.poll_interval, stop), daemon=True)
    t.start()

    while True:
        print(MENU, end="")
        choice = input("> ").strip()
        if choice == "0":
            print("  Até logo!")
            stop.set()
            break
        action = ACTIONS.get(choice)
        if action:
            try:
                action(token)
            except Exception as e:
                err(str(e))
        else:
            print("  Opção inválida.")

if __name__ == "__main__":
    main()
