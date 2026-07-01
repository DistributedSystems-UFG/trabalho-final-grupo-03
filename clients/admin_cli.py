# clients/admin_cli.py
import argparse, json, os, sys, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "stubs"))
import auth_http, admin_http, notification_http

# ── helpers de UI ─────────────────────────────────────────────────────────────

def hr():       print("─" * 60)
def ok(msg):    print(f"  ✔ {msg}")
def err(msg):   print(f"  ✘ {msg}", file=sys.stderr)
def title(msg): print(f"\n{'─'*60}\n  {msg}\n{'─'*60}")

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

# ── ações ─────────────────────────────────────────────────────────────────────

def do_health(token: str):
    result = admin_http.get_health()
    print(f"\n  Gateway: {result}")

def do_db_status(token: str):
    title("Topologia de Shards")
    result = admin_http.get_db_status(token)
    if "error" in result:
        err(f"Erro: {result['error']}")
        return
    shards = result.get("shards", [])
    for s in shards:
        failover = "⚠  FAILOVER ATIVO" if s.get("failover_active") else "✔ normal"
        print(f"\n  Shard: {s['shard_id']}")
        print(f"    Principal:  {s['primary_id']}")
        print(f"    Réplicas:   {', '.join(s['replica_ids']) or '(nenhuma)'}")
        print(f"    Status:     {failover}")

def do_promote(token: str):
    title("Promoção Manual de Réplica")
    print("  Shards disponíveis: shard_a | shard_b | shard_c")
    shard_id   = input("  shard_id:   ").strip()
    replica_id = input("  replica_id: ").strip()
    if not shard_id or not replica_id:
        err("shard_id e replica_id são obrigatórios.")
        return
    confirm = input(f"\n  Promover réplica '{replica_id}' no '{shard_id}'? [s/N] ").strip().lower()
    if confirm != "s":
        print("  Cancelado.")
        return
    result = admin_http.promote_replica(token, shard_id, replica_id)
    if "error" in result:
        err(f"Falha: {result['error']}")
    else:
        ok(f"Promoção concluída. Novo principal: {result.get('new_primary', replica_id)}")

def do_raw_status(token: str):
    title("Status RAW (JSON)")
    result = admin_http.get_db_status(token)
    print(json.dumps(result, indent=2, ensure_ascii=False))

def do_notifications(token: str):
    result = notification_http.get_notifications(token)
    notifs = result if isinstance(result, list) else []
    if not notifs:
        print("  Sem notificações.")
        return
    for n in notifs:
        print(f"  {n['message']}  ({n['created_at']})")
    notification_http.mark_read(token, [n["id"] for n in notifs])
    ok(f"{len(notifs)} marcada(s) como lida(s).")

# ── menu ──────────────────────────────────────────────────────────────────────

MENU = """
  1. Health check
  2. Status dos shards
  3. Promover réplica
  4. Status RAW (JSON)
  5. Notificações
  0. Sair
"""

ACTIONS = {
    "1": do_health,
    "2": do_db_status,
    "3": do_promote,
    "4": do_raw_status,
    "5": do_notifications,
}

# ── autenticação ──────────────────────────────────────────────────────────────

def authenticate(admin_token: str | None) -> str:
    if admin_token:
        result = admin_http.get_health()
        if "error" not in result:
            ok(f"Conectado com token de admin.")
            return admin_token
        err("Token inválido ou Gateway inacessível.")
        sys.exit(1)

    title("SCD Inventário — Admin")
    print("  1. Login\n  2. Token direto")
    choice = input("\n> ").strip()
    if choice == "2":
        token = input("  Token admin: ").strip()
        return token
    username = input("  Usuário: ").strip()
    password = input("  Senha:   ").strip()
    result = auth_http.login(username, password)
    if "error" in result:
        err(f"Falha: {result['error']}")
        sys.exit(1)
    ok(f"Login efetuado! id={result['id'][:8]}...")
    return result["token"]

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CLI Admin — SCD Inventário")
    parser.add_argument("--gateway-url", default="http://localhost:8080")
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--token", default=None, help="Token admin direto (env: GATEWAY_TOKEN)")
    args = parser.parse_args()
    os.environ["GATEWAY_URL"] = args.gateway_url

    admin_token = args.token or os.getenv("GATEWAY_TOKEN")
    token = authenticate(admin_token)

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
