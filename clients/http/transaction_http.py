from _http import _request

def buy(token: str, product_id: str, quantity: int) -> dict:
    """POST /orders → {"id": str, "total_price": float, "status": "confirmed"} | 409"""
    return _request("POST", "/orders",
                    {"product_id": product_id, "quantity": quantity}, token=token)

def list_orders(token: str) -> dict:
    """GET /orders → [{"id", "product_id", "quantity", "total_price", "status", "created_at"}]"""
    return _request("GET", "/orders", token=token)

if __name__ == "__main__":
    import os
    tok = os.getenv("GATEWAY_TOKEN", "TOKEN_EXEMPLO")
    print("=== list_orders ===")
    print(list_orders(tok))
    print("=== buy (produto exemplo, qtd 1) ===")
    print(buy(tok, product_id="produto-uuid-aqui", quantity=1))
