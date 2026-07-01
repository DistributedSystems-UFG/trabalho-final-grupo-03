from _http import _request

# --- Produtos ---

def list_products(token: str, category: str | None = None, name: str | None = None) -> dict:
    """GET /products[?category=&name=] → [{"id", "name", "category", "price", "quantity", "seller_id"}]"""
    params = "&".join(f"{k}={v}" for k, v in [("category", category), ("name", name)] if v)
    path = "/products" + (f"?{params}" if params else "")
    return _request("GET", path, token=token)

def get_product(token: str, product_id: str) -> dict:
    """GET /products/{id} → {"id", "name", "category", "price", "quantity", "seller_id", "alerta_quantidade"}"""
    return _request("GET", f"/products/{product_id}", token=token)

def create_product(token: str, name: str, description: str, category: str,
                   price: float, quantity: int, alerta_quantidade: int) -> dict:
    """POST /products → {"id": str}"""
    body = {"name": name, "description": description, "category": category,
            "price": price, "quantity": quantity, "alerta_quantidade": alerta_quantidade}
    return _request("POST", "/products", body, token=token)

def update_product(token: str, product_id: str, **fields) -> dict:
    """PUT /products/{id} → {"ok": true}"""
    return _request("PUT", f"/products/{product_id}", fields, token=token)

def delete_product(token: str, product_id: str) -> dict:
    """DELETE /products/{id} → {"ok": true}"""
    return _request("DELETE", f"/products/{product_id}", token=token)

# --- Watchlist ---

def add_watchlist(token: str, product_id: str, max_price: float) -> dict:
    """POST /watchlist → {"id": str}"""
    return _request("POST", "/watchlist",
                    {"product_id": product_id, "max_price": max_price}, token=token)

def list_watchlist(token: str) -> dict:
    """GET /watchlist → [{"id", "product_id", "max_price", "notified"}]"""
    return _request("GET", "/watchlist", token=token)

def remove_watchlist(token: str, watchlist_id: str) -> dict:
    """DELETE /watchlist/{id} → {"ok": true}"""
    return _request("DELETE", f"/watchlist/{watchlist_id}", token=token)

# --- Ofertas relâmpago ---

def create_flash_offer(token: str, product_id: str,
                       discount_pct: float, duration_minutes: int) -> dict:
    """POST /flash-offers → {"id": str, "promo_price": float, "expires_at": str}"""
    body = {"product_id": product_id,
            "discount_pct": discount_pct, "duration_minutes": duration_minutes}
    return _request("POST", "/flash-offers", body, token=token)

def list_flash_offers(token: str, category: str | None = None) -> dict:
    """GET /flash-offers[?category=] → [{"id", "product_id", "promo_price", "expires_at"}]"""
    path = "/flash-offers" + (f"?category={category}" if category else "")
    return _request("GET", path, token=token)

if __name__ == "__main__":
    import os
    tok = os.getenv("GATEWAY_TOKEN", "TOKEN_EXEMPLO")
    print("=== list_products ===")
    print(list_products(tok))
    print("=== list_flash_offers ===")
    print(list_flash_offers(tok))
