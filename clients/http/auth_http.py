from _http import _request

def register(username: str, password: str, role: str) -> dict:
    """POST /auth/register → {"id": ..., "token": ...}"""
    return _request("POST", "/auth/register",
                    {"username": username, "password": password, "role": role})

def login(username: str, password: str) -> dict:
    """POST /auth/login → {"id": ..., "token": ...}"""
    return _request("POST", "/auth/login",
                    {"username": username, "password": password})

if __name__ == "__main__":
    print("=== register ===")
    print(register("alice", "s3cr3t", "buyer"))
    print("=== login ===")
    print(login("alice", "s3cr3t"))
