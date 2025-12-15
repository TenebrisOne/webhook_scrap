# odoo_rpc.py
import requests
import logging
from typing import Tuple, Optional
from dotenv import load_dotenv
import logging, os, sys
from logging.handlers import RotatingFileHandler
# --- LOG A CONSOLA ---

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()  # DEBUG/INFO/WARNING/ERROR

# Limpia handlers previos (por si uvicorn añade los suyos)
for h in logging.root.handlers[:]:
    logging.root.removeHandler(h)

handler = logging.StreamHandler(stream=sys.stdout)
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
handler.setFormatter(formatter)

root = logging.getLogger()
root.setLevel(LOG_LEVEL)
root.addHandler(handler)
log = logging.getLogger("odoo_rpc")  # tu logger de app


load_dotenv()  # Carga el archivo .env

ODOO_JSONRPC = os.getenv("ODOO_JSONRPC")
DB = os.getenv("DB")
UID = int(os.getenv("UID"))
PWD = os.getenv("PWD")





def post_write(partner_id: int, field_name: str, url_value: Optional[str]) -> Tuple[bool, dict]:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "call",
        "params": {
            "service": "object",
            "method": "execute_kw",
            "args": [DB, UID, PWD, "res.partner", "write", [[partner_id], {field_name: url_value}]],
        },
    }
    # LOG para ver exactamente lo que se envía
    log.info({"event": "odoo_post_write_payload", "payload": payload})

    
    try:
        r = requests.post(ODOO_JSONRPC, json=payload, timeout=20)
        status = r.status_code
        text = r.text
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.RequestException as e:
        return False, {"error": "http_error", "detail": str(e)}
    except ValueError:
        return False, {"error": "json_decode_error", "status": status, "body": text}

    if "error" in data:
        return False, data["error"]
    return bool(data.get("result")), data

def read_fields(partner_id: int, fields: list[str]) -> Tuple[bool, dict]:
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "call",
        "params": {
            "service": "object",
            "method": "execute_kw",
            "args": [DB, UID, PWD, "res.partner", "read", [[partner_id], fields]],
        },
    }
    
    
    try:
        r = requests.post(ODOO_JSONRPC, json=payload, timeout=20)
        status = r.status_code
        text = r.text
        r.raise_for_status()
        data = r.json()
    except requests.exceptions.RequestException as e:
        return False, {"error": "http_error", "detail": str(e)}
    except ValueError:
        return False, {"error": "json_decode_error", "status": status, "body": text}

    if "error" in data:
        return False, data["error"]
    return True, data.get("result")

if __name__ == "__main__":
    import argparse, json
    
    parser = argparse.ArgumentParser(description="Prueba de funciones de Odoo RPC")
    parser.add_argument("--partner-id", type=int, required=True, help="ID del partner en Odoo")
    parser.add_argument("--field", required=True, help="Nombre del campo a escribir/leer")
    parser.add_argument("--value", default="", help="Valor a escribir en el campo")
    args = parser.parse_args()

    # Escribir en Odoo
    ok_w, w_raw = post_write(args.partner_id, args.field, args.value)
    print(json.dumps({"ok_write": ok_w, "write_raw": w_raw}, indent=2))

    # Leer desde Odoo
    ok_r, r_raw = read_fields(args.partner_id, [args.field])
    print(json.dumps({"ok_read": ok_r, "read_raw": r_raw}, indent=2))
