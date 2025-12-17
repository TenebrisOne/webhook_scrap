# odoo_rpc.py
import requests
import logging
from typing import Tuple, Optional
from dotenv import load_dotenv
import os

load_dotenv()  # Carga el archivo .env

ODOO_JSONRPC = os.getenv("ODOO_JSONRPC")
DB = os.getenv("DB")
UID = int(os.getenv("UID"))
PWD = os.getenv("PWD")

log = logging.getLogger("odoo_rpc")

def _post(payload: dict) -> Tuple[bool, dict]:
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
    return True, data

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
    log.info({"event": "odoo_post_write_payload", "payload": payload})
    return _post(payload)

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
    return _post(payload)

def post_write_multi(partner_id: int, vals: dict) -> Tuple[bool, dict]:
    """
    Escribe varios campos en una sola llamada Odoo JSON-RPC.
    Respeta credenciales del .env (DB, UID, PWD, ODOO_JSONRPC).
    """
    if not isinstance(vals, dict):
        vals = {}
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "call",
        "params": {
            "service": "object",
            "method": "execute_kw",
            "args": [DB, UID, PWD, "res.partner", "write", [[partner_id], vals]],
        },
    }
    logging.getLogger("odoo_rpc").info({"event": "odoo_post_write_multi_payload", "payload": payload})
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


if __name__ == "__main__":
    import argparse, json
    parser = argparse.ArgumentParser(description="Prueba de funciones de Odoo RPC")
    parser.add_argument("--partner-id", type=int, required=True, help="ID del partner en Odoo")
    parser.add_argument("--field", required=False, help="Nombre del campo a escribir/leer")
    parser.add_argument("--value", default="", help="Valor a escribir en el campo")
    parser.add_argument("--read", action="store_true")
    parser.add_argument("--multi", action="store_true")
    args = parser.parse_args()

    if args.read:
        ok_r, r_raw = read_fields(args.partner_id, [args.field] if args.field else ["name"])
        print(json.dumps({"ok_read": ok_r, "read_raw": r_raw}, indent=2))
    elif args.multi:
        ok_w, w_raw = post_write_multi(args.partner_id, {"name": args.value})
        print(json.dumps({"ok_write_multi": ok_w, "write_raw": w_raw}, indent=2))
    else:
        ok_w, w_raw = post_write(args.partner_id, args.field, args.value)
        print(json.dumps({"ok_write": ok_w, "write_raw": w_raw}, indent=2))
