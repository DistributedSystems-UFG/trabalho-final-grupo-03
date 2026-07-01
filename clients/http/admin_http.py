from _http import _request

def get_health() -> dict:
    """GET /health → {"status": "ok"}"""
    return _request("GET", "/health")

def get_db_status(token: str) -> dict:
    """GET /admin/db-status → {"shards": [{"shard_id", "primary_id", "replica_ids", "failover_active"}]}"""
    return _request("GET", "/admin/db-status", token=token)

def promote_replica(token: str, shard_id: str, replica_id: str) -> dict:
    """POST /admin/promote-replica → {"ok": true, "new_primary": str}"""
    return _request("POST", "/admin/promote-replica",
                    {"shard_id": shard_id, "replica_id": replica_id}, token=token)

if __name__ == "__main__":
    import os
    tok = os.getenv("GATEWAY_TOKEN", "TOKEN_ADMIN")
    print("=== health ===")
    print(get_health())
    print("=== db_status ===")
    print(get_db_status(tok))
