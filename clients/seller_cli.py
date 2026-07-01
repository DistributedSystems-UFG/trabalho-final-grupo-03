# clients/seller_cli.py
import argparse, os, sys, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
import auth_http, inventory_http, notification_http

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

def my_products(token: str, seller_id: str) -> list:
    result = inventory_http.list_products(token)
    all_prods = result if isinstance(result, list) else []
    return [p for p in all_prods if p.get("seller_id") == seller_id]

def find_product(token: str, seller_id: str, query: str) -> dict | None:
    matches = [p for p in my_products(token, seller_id) if query.lower() in p["name"].lower()]
    if not matches:
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

def prompt_product(token: str, seller_id: str) -> dict | None:
    name = input("  Nome do produto: ").strip()
    if not name:
        err("Nome não pode ser vazio.")
        return None
    return find_product(token, seller_id, name)

# ── ações ─────────────────────────────────────────────────────────────────────

def do_list_my_products(token: str, seller_id: str):
    products = my_products(token, seller_id)
    if not products:
        print("  Você não tem produtos cadastrados.")
        return
    print(f"\n  {len(products)} produto(s):")
    for p in products:
        print(f"  {p['name']:<24} R${p['price']:>8.2f}  qty={p['quantity']}  [{p['category']}]  alerta={p.get('alerta_quantidade','?')}")

def do_create_product(token: str, seller_id: str):
    name        = input("  Nome:              ").strip()
    description = input("  Descrição:         ").strip()
    print(f"  Categorias: {' | '.join(CATEGORIES)}")
    category    = input("  Categoria:         ").strip()
    price       = input("  Preço (R$):        ").strip()
    quantity    = input("  Quantidade:        ").strip()
    alerta      = input("  Alerta de estoque: ").strip()
    try:
        result = inventory_http.create_product(
            token, name, description, category,
            float(price), int(quantity), int(alerta)
        )
        if "error" in result:
            err(f"Erro: {result['error']}")
        else:
            ok(f"Produto '{name}' criado.")
    except ValueError:
        err("Valores inválidos.")

def do_edit_product(token: str, seller_id: str):
    product = prompt_product(token, seller_id)
    if not product:
        return
    print(f"  Editando: {product['name']} (atual: R${product['price']:.2f}, qty={product['quantity']}, alerta={product['alerta_quantidade']})")
    price    = input("  Novo preço (R$, Enter para manter): ").strip()
    quantity = input("  Nova quantidade (Enter para manter): ").strip()
    alerta   = input("  Novo alerta (Enter para manter): ").strip()
    try:
        fields = {
            "price":             float(price)    if price    else product["price"],
            "quantity":          int(quantity)   if quantity else product["quantity"],
            "alerta_quantidade": int(alerta)     if alerta   else product["alerta_quantidade"],
        }
        result = inventory_http.update_product(token, product["id"], **fields)
        if "error" in result:
            err(f"Erro: {result['error']}")
        else:
            ok("Produto atualizado.")
    except ValueError:
        err("Valores inválidos.")

def do_delete_product(token: str, seller_id: str):
    product = prompt_product(token, seller_id)
    if not product:
        return
    confirm = input(f"  Confirma exclusão de '{product['name']}'? [s/N] ").strip().lower()
    if confirm != "s":
        print("  Cancelado.")
        return
    result = inventory_http.delete_product(token, product["id"])
    if "error" in result:
        err(f"Erro: {result['error']}")
    else:
        ok("Produto removido.")

def do_my_orders(token: str, seller_id: str):
    # importa transaction_http aqui para manter o import no topo enxuto
    import transaction_http as ts
    result = ts.list_orders(token)
    orders = result if isinstance(result, list) else []
    if not orders:
        print("  Nenhuma venda encontrada.")
        return
    print(f"\n  {len(orders)} venda(s):")
    for o in orders:
        prod = inventory_http.get_product(token, o["product_id"])
        name = prod.get("name", o["product_id"][:8]) if isinstance(prod, dict) and "name" in prod else o["product_id"][:8]
        print(f"  {name:<24} qty={o['quantity']}  total=R${o['total_price']:.2f}  comprador={o['buyer_id'][:8]}...  {o['created_at']}")

def do_create_flash_offer(token: str, seller_id: str):
    product = prompt_product(token, seller_id)
    if not product:
        return
    print(f"  Produto: {product['name']}  (preço atual: R${product['price']:.2f})")
    discount  = input("  Desconto (%):   ").strip()
    duration  = input("  Duração (min):  ").strip()
    try:
        result = inventory_http.create_flash_offer(token, product["id"], float(discount), int(duration))
        if "error" in result:
            err(f"Erro: {result['error']}")
        else:
            ok(f"Oferta criada: promo=R${result['promo_price']:.2f}  expira={result['expires_at']}")
    except ValueError:
        err("Valores inválidos.")

def do_my_flash_offers(token: str, seller_id: str):
    result = inventory_http.list_flash_offers(token)
    offers = result if isinstance(result, list) else []
    if not offers:
        print("  Nenhuma oferta relâmpago ativa.")
        return
    print(f"\n  {len(offers)} oferta(s):")
    for f in offers:
        prod = inventory_http.get_product(token, f["product_id"])
        name = prod.get("name", f["product_id"][:8]) if isinstance(prod, dict) and "name" in prod else f["product_id"][:8]
        print(f"  {name:<24} promo=R${f['promo_price']:.2f}  expira={f['expires_at']}")

def do_notifications(token: str, seller_id: str):
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
  1. Meus produtos
  2. Cadastrar produto
  3. Editar produto
  4. Remover produto
  5. Minhas vendas
  6. Criar oferta relâmpago
  7. Minhas ofertas relâmpago
  8. Notificações
  0. Sair
"""

ACTIONS = {
    "1": do_list_my_products,
    "2": do_create_product,
    "3": do_edit_product,
    "4": do_delete_product,
    "5": do_my_orders,
    "6": do_create_flash_offer,
    "7": do_my_flash_offers,
    "8": do_notifications,
}

# ── autenticação ──────────────────────────────────────────────────────────────

def authenticate() -> tuple[str, str]:
    title("SCD Inventário — Vendedor")
    print("  1. Login\n  2. Cadastro")
    choice = input("\n> ").strip()
    username = input("  Usuário: ").strip()
    password = input("  Senha:   ").strip()
    if choice == "2":
        result = auth_http.register(username, password, "seller")
    else:
        result = auth_http.login(username, password)
    if "error" in result:
        err(f"Falha: {result['error']}")
        sys.exit(1)
    ok(f"Bem-vindo! id={result['id'][:8]}...")
    return result["id"], result["token"]

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CLI do Vendedor — SCD Inventário")
    parser.add_argument("--gateway-url", default="http://localhost:8080")
    parser.add_argument("--poll-interval", type=int, default=5)
    args = parser.parse_args()
    os.environ["GATEWAY_URL"] = args.gateway_url

    seller_id, token = authenticate()

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
                action(token, seller_id)
            except Exception as e:
                err(str(e))
        else:
            print("  Opção inválida.")

if __name__ == "__main__":
    main()
