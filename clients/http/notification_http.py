from _http import _request

def get_notifications(token: str) -> dict:
    """GET /notifications → [{"id", "message", "created_at"}]"""
    return _request("GET", "/notifications", token=token)

def mark_read(token: str, ids: list[str]) -> dict:
    """PATCH /notifications/read → {"ok": true}"""
    return _request("PATCH", "/notifications/read", {"ids": ids}, token=token)

if __name__ == "__main__":
    import os
    tok = os.getenv("GATEWAY_TOKEN", "TOKEN_EXEMPLO")
    print("=== get_notifications ===")
    result = get_notifications(tok)
    print(result)
    notif_ids = [n["id"] for n in (result if isinstance(result, list) else [])]
    if notif_ids:
        print("=== mark_read ===")
        print(mark_read(tok, notif_ids))
