import json
import os
import urllib.request
import urllib.error

_DEFAULT_GATEWAY_URL = "http://localhost:8080"

def _request(method: str, path: str, body: dict | None = None, token: str | None = None) -> dict:
    """Envia uma requisição HTTP ao Gateway e retorna o corpo como dict.

    Retorno:
        dict com o JSON da resposta em caso de sucesso.
        {"error": "<msg>", "status": <código>} em caso de erro HTTP.
        {"error": "<msg>", "status": 0}        em caso de falha de rede.
    """
    gateway_url = os.getenv("GATEWAY_URL", _DEFAULT_GATEWAY_URL)
    url = gateway_url + path
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    tok = token or os.getenv("GATEWAY_TOKEN", "")
    if tok:
        headers["X-Auth-Token"] = tok
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
        except Exception:
            detail = e.reason
        return {"error": detail, "status": e.code}
    except urllib.error.URLError as e:
        return {"error": str(e.reason), "status": 0}
