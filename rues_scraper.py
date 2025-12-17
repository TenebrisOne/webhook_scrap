# webhook_server.py
import os
import re
import logging
from typing import Dict, Any, Optional, Tuple, List

import requests
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from odoo_rpc import post_write_multi, read_fields
from datetime import datetime, timezone

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = app.logger

# -------- Config --------
SOCRATA_URL = os.getenv("SOCRATA_URL", "https://www.datos.gov.co/resource/c82u-588k.json")
SOCRATA_APP_TOKEN = os.getenv("SOCRATA_APP_TOKEN")
RUES_DETALLE_URLS = [
    os.getenv("RUES_DETALLE_URL", "https://ruesapi.rues.org.co/WEB2/api/Expediente/DetalleRM/{}"),
    "https://ruesapi.rues.org.co/WEB/api/Expediente/DetalleRM/{}",
]
RUES_BASE_WEB = os.getenv("RUES_BASE_WEB", "https://www.rues.org.co")
TIMEOUT = int(os.getenv("TIMEOUT", "12"))
RUES_UA = os.getenv("RUES_USER_AGENT", "Mozilla/5.0 (RUES-Scraper/1.0)")

# Campos Odoo destino (ajústalos por ENV si difieren)
ODOO_FIELD_NOMBRE_COMERCIAL = os.getenv("ODOO_FIELD_NOMBRE_COMERCIAL", "x_studio_nombre_comercial")
ODOO_FIELD_FECHA_MATRICULA = os.getenv("ODOO_FIELD_FECHA_MATRICULA", "x_studio_fecha_de_matricula")
ODOO_FIELD_CIIU = os.getenv("ODOO_FIELD_CIIU", "x_studio_ciiu")
ODOO_FIELD_REPRESENTANTE = os.getenv("ODOO_FIELD_REPRESENTANTE", "x_studio_representante_legal_1")

SESSION_HEADERS = {
    "User-Agent": RUES_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-CO,es;q=0.9",
    "Connection": "keep-alive",
    "Referer": f"{RUES_BASE_WEB}/",
}

# -------- Helpers --------
def only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def nit_base_sin_dv(s: str) -> str:
    d = only_digits(s)
    return d[:-1] if len(d) >= 9 else d

def extract_nit_from_payload(data: Dict[str, Any]) -> Optional[str]:
    nit = data.get("nit")
    vat = data.get("vat")
    if nit and isinstance(nit, str) and nit.strip():
        return nit.strip()
    if isinstance(vat, (int, float)):
        return str(int(vat))
    if isinstance(vat, str) and vat.strip():
        return vat.strip()
    return None

def _to_iso_date(value: Any) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip()
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.date().isoformat()
    except Exception:
        pass
    m = re.match(r"^/Date\((\d+)\)/$", s)
    if m:
        try:
            ms = int(m.group(1))
            dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
            return dt.date().isoformat()
        except Exception:
            pass
    if s.isdigit():
        try:
            val = int(s)
            dt = datetime.fromtimestamp(val / 1000 if val > 10_000_000_000 else val, tz=timezone.utc)
            return dt.date().isoformat()
        except Exception:
            pass
    try:
        return datetime.strptime(s, "%d/%m/%Y").date().isoformat()
    except Exception:
        return None

def socrata_headers() -> Dict[str, str]:
    return {"X-App-Token": SOCRATA_APP_TOKEN} if SOCRATA_APP_TOKEN else {}

def fetch_socrata(nit_base: str) -> Optional[Dict[str, Any]]:
    params = {"$select": "nit,razon_social,sigla,codigo_camara,matricula", "nit": nit_base, "$limit": 5}
    r = requests.get(SOCRATA_URL, params=params, headers=socrata_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json() or []
    log.info({"event": "socrata_response", "count": len(data), "nit": nit_base, "sample": (data[0] if data else None)})
    if not data:
        return None
    try:
        data.sort(key=lambda x: int((x.get("matricula") or "0") or 0), reverse=True)
    except Exception:
        pass
    return data[0]

def build_id_rm(codigo_camara: str, matricula: str) -> Optional[str]:
    try:
        return f"{int(codigo_camara):02d}{int(matricula):010d}"
    except Exception:
        return None

def unwrap_rues_registro(js: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(js, dict):
        return {}
    if "registros" in js:
        regs = js.get("registros")
        if isinstance(regs, list) and regs:
            reg = regs[0]
            return reg if isinstance(reg, dict) else {}
        if isinstance(regs, dict):
            return regs
    if "registro" in js and isinstance(js["registro"], dict):
        return js["registro"]
    return js

def fetch_rues_detalle_api(id_rm: str) -> Dict[str, Any]:
    for tpl in RUES_DETALLE_URLS:
        url = tpl.format(id_rm)
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": RUES_UA})
            log.info({"event": "rues_detalle_http", "url": url, "status": r.status_code})
            if r.status_code != 200:
                continue
            js = r.json() or {}
            reg = unwrap_rues_registro(js)
            if isinstance(reg, dict) and reg:
                return reg
        except Exception as e:
            log.warning({"event": "rues_detalle_error", "url": url, "error": str(e)})
    return {}

def _first_nonempty_str(*vals: Any) -> Optional[str]:
    for v in vals:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def extract_name_sigla(detalle: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    empresa = detalle.get("empresa") if isinstance(detalle.get("empresa"), dict) else None
    razon_social = _first_nonempty_str(
        detalle.get("razonSocial"),
        detalle.get("razon_social"),
        empresa.get("razonSocial") if empresa else None,
        empresa.get("razon_social") if empresa else None,
    )
    sigla = _first_nonempty_str(detalle.get("sigla"), empresa.get("sigla") if empresa else None)
    return razon_social, sigla

def extract_rues_extras(detalle: Dict[str, Any]) -> Dict[str, Optional[str]]:
    # fecha
    fecha = _first_nonempty_str(
        detalle.get("fechaMatricula"),
        detalle.get("fecha_matricula"),
        detalle.get("fechaMatriculaRegistro"),
        detalle.get("fechaInscripcion"),
        detalle.get("fechaConstitucion"),
    )
    if not fecha and isinstance(detalle.get("empresa"), dict):
        emp = detalle["empresa"]
        fecha = _first_nonempty_str(emp.get("fechaMatricula"), emp.get("fechaInscripcion"), emp.get("fechaConstitucion"))
    fecha_iso = _to_iso_date(fecha)

    # ciiu
    ciiu_code: Optional[str] = None
    posibles = [detalle.get("actividadesEconomicas"), detalle.get("actividades"), detalle.get("actividadEconomica")]
    if not any(posibles) and isinstance(detalle.get("empresa"), dict):
        emp = detalle["empresa"]
        posibles = [emp.get("actividadesEconomicas"), emp.get("actividades"), emp.get("actividadEconomica")]
    for lst in posibles:
        if isinstance(lst, list) and lst:
            item0 = lst[0]
            if isinstance(item0, dict):
                ciiu_code = _first_nonempty_str(item0.get("codigoCIIU"), item0.get("ciiu"), item0.get("codigo"), item0.get("codigoCiiu"))
                if ciiu_code:
                    break
        elif isinstance(lst, dict):
            ciiu_code = _first_nonempty_str(lst.get("codigoCIIU"), lst.get("ciiu"), lst.get("codigo"), lst.get("codigoCiiu"))
            if ciiu_code:
                break

    # representante
    rep: Optional[str] = None
    candidatos = [
        detalle.get("representantesLegales"),
        detalle.get("representantes"),
        detalle.get("apoderados"),
        detalle.get("junta"),
        detalle.get("personas"),
    ]
    if all(not x for x in candidatos) and isinstance(detalle.get("empresa"), dict):
        emp = detalle["empresa"]
        candidatos = [emp.get("representantesLegales"), emp.get("representantes"), emp.get("apoderados"), emp.get("junta"), emp.get("personas")]
    nombres: List[str] = []
    for bloque in candidatos:
        if isinstance(bloque, list):
            for p in bloque:
                if not isinstance(p, dict):
                    continue
                nombre = _first_nonempty_str(p.get("nombre"), p.get("nombreCompleto"), p.get("razonSocial"), p.get("nombres"))
                if nombre:
                    rol = _first_nonempty_str(p.get("rol"), p.get("cargo"), p.get("tipo"))
                    if (rol or "").lower().find("represent") >= 0:
                        nombres.append(nombre)
                    elif not nombres:
                        nombres.append(nombre)
        elif isinstance(bloque, dict):
            nombre = _first_nonempty_str(bloque.get("nombre"), bloque.get("nombreCompleto"), bloque.get("razonSocial"), bloque.get("nombres"))
            if nombre:
                nombres.append(nombre)
    if nombres:
        rep = ", ".join(dict.fromkeys([n.strip() for n in nombres if n]).keys())  # únicos y orden

    return {"fecha_matricula": fecha_iso, "ciiu": ciiu_code, "representante_legal": rep}

# ---- HTML fallback para representante y CIIU ----
def find_value_by_label_in_soup(soup: BeautifulSoup, label_regex: str) -> Optional[str]:
    lbl = soup.find(string=re.compile(label_regex, re.I))
    if not lbl:
        return None
    node = getattr(lbl, "parent", None)
    if node:
        for sib in node.next_elements:
            if isinstance(sib, str):
                val = sib.strip()
                if val and not re.search(label_regex, val, re.I):
                    return re.sub(r"\s+", " ", val)
    return None

def _extract_representante_from_soup(soup: BeautifulSoup) -> Optional[str]:
    text = soup.get_text("\n", strip=True)
    # Busca filas tipo "Representación legal" o tablas con "Cargo / Nombre"
    m = re.search(r"Representaci[oó]n\s+legal.*?\n(.*)", text, re.I | re.S)
    if m:
        block = m.group(1)[:800]
        # Primer nombre propio con varias palabras en mayúscula inicial
        nm = re.search(r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ']+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ']+){1,4})", block)
        if nm:
            return nm.group(1).strip()
    # Tablas
    for tr in soup.find_all("tr"):
        row = " ".join(td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"]))
        if re.search(r"represent", row, re.I):
            nm = re.search(r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ']+(?:\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ']+){1,4})", row)
            if nm:
                return nm.group(1).strip()
    return None

def fetch_detail_from_web_id(web_id: Any) -> Dict[str, Optional[str]]:
    try:
        did = int(str(web_id).strip())
    except Exception:
        return {}
    url = f"{RUES_BASE_WEB}/detalle/{did}/"
    r = requests.get(url, headers=SESSION_HEADERS, timeout=TIMEOUT)
    log.info({"event": "html_detail_by_id_http", "url": url, "status": r.status_code})
    if r.status_code != 200 or not r.text:
        return {}
    s2 = BeautifulSoup(r.text, "html.parser")
    razon_social = None
    for sel in ["h1", "h2", "p.font-rues-large.filtro__titulo"]:
        el = s2.select_one(sel)
        if el and el.get_text(strip=True):
            razon_social = el.get_text(strip=True)
            break
    sigla = find_value_by_label_in_soup(s2, r"^\s*sigla\s*$") or find_value_by_label_in_soup(s2, r"sigla")
    fecha = (find_value_by_label_in_soup(s2, r"fecha\s+de\s+matr[íi]cula")
             or find_value_by_label_in_soup(s2, r"fecha\s+de\s+inscripci[óo]n")
             or find_value_by_label_in_soup(s2, r"fecha\s+de\s+constituci[óo]n"))
    fecha_iso = _to_iso_date(fecha)
    ciiu = None
    a_code = s2.find("a", string=re.compile(r"^\s*\d{4}\s*$"))
    if a_code:
        mc = re.findall(r"\d{4}", a_code.get_text())
        if mc:
            ciiu = mc[0]
    if not ciiu:
        m = re.search(r"\b(\d{4})\b", s2.get_text(" ", strip=True))
        if m:
            ciiu = m.group(1)
    representante = _extract_representante_from_soup(s2)
    parsed = {
        "razon_social": razon_social,
        "sigla": sigla,
        "fecha_matricula": fecha_iso,
        "ciiu": ciiu,
        "representante_legal": representante,
    }
    log.info({"event": "html_detail_by_id_parsed", "parsed": {k: v for k, v in parsed.items() if v}, "url": url})
    return parsed

# -------- Endpoint principal --------
@app.post("/webhook")
def receive_webhook():
    data = request.get_json(force=True, silent=False)

    # 1) partner_id (igual que tu server anterior)
    partner_id = data.get("id") or data.get("_id") or data.get("record_id") or ((data.get("data") or {}).get("id") if isinstance(data.get("data"), dict) else None)
    if not partner_id:
        return jsonify({"error": "missing_partner_id", "detail": "El payload debe traer 'id' (o _id / record_id / data.id)"}), 400
    partner_id = int(partner_id)

    # 2) NIT / VAT
    raw_nit = extract_nit_from_payload(data)
    nit_digits = nit_base_sin_dv(raw_nit or "")
    if not nit_digits:
        return jsonify({"error": "missing_nit", "detail": "Envía 'nit' o 'vat' en el payload"}), 400

    log.info({"event": "webhook_received", "partner_id": partner_id, "nit": raw_nit, "nit_digits": nit_digits})

    # 3) (opcional) validar partner existe
    try:
        exists, _ = read_fields(partner_id, ["id"])
        if not exists:
            return jsonify({"error": "partner_not_found"}), 404
    except Exception as e:
        log.warning(f"No se pudo validar existencia del partner: {e}")

    # 4) Datos básicos via Socrata
    try:
        row = fetch_socrata(nit_digits)
    except requests.HTTPError:
        row = None

    razon_social = None
    sigla = None
    fecha_matricula = None
    ciiu = None
    representante_legal = None

    detalle: Dict[str, Any] = {}
    if row and row.get("codigo_camara") and row.get("matricula"):
        id_rm = build_id_rm(row["codigo_camara"], row["matricula"])
        log.info({"event": "id_rm_built", "codigo_camara": row.get("codigo_camara"), "matricula": row.get("matricula"), "id_rm": id_rm})
        if id_rm:
            detalle = fetch_rues_detalle_api(id_rm)

    if detalle:
        name_d, sigla_d = extract_name_sigla(detalle)
        name_d = name_d or (detalle.get("razon_social") or "").strip() or None
        sigla_d = sigla_d or (detalle.get("sigla") or "").strip() or None
        razon_social = (row.get("razon_social") if row else None) or name_d
        sigla = (row.get("sigla") if row else None) or sigla_d

        extras = extract_rues_extras(detalle)
        fecha_matricula = extras.get("fecha_matricula")
        ciiu = extras.get("ciiu") or ciiu
        representante_legal = extras.get("representante_legal") or representante_legal

        if not (ciiu and representante_legal):
            web_id = detalle.get("id") or detalle.get("id_detalle") or detalle.get("id_detalle_web")
            if web_id is not None:
                html_by_id = fetch_detail_from_web_id(web_id)
                if html_by_id:
                    razon_social = razon_social or html_by_id.get("razon_social")
                    sigla = sigla or html_by_id.get("sigla")
                    fecha_matricula = fecha_matricula or html_by_id.get("fecha_matricula")
                    ciiu = ciiu or html_by_id.get("ciiu")
                    representante_legal = representante_legal or html_by_id.get("representante_legal")
    else:
        # Si Socrata no devolvió o API falló, intenta HTML directo de búsqueda (simple)
        # Para no crecer más el código, aquí devolvemos 404 (puedes extender con search si lo necesitas)
        log.warning({"event": "no_detalle_api"})
        return jsonify({"error": "not_found", "detail": f"No encontré datos para NIT {nit_digits}"}), 404

    if not (razon_social or sigla or fecha_matricula or ciiu or representante_legal):
        return jsonify({"error": "not_found", "detail": f"No encontré datos para NIT {nit_digits}"}), 404

    # 5) Escribir en Odoo en UNA sola llamada
    vals = {
        "name": razon_social,
        ODOO_FIELD_NOMBRE_COMERCIAL: sigla,
        ODOO_FIELD_FECHA_MATRICULA: fecha_matricula,
        ODOO_FIELD_CIIU: ciiu,
        ODOO_FIELD_REPRESENTANTE: representante_legal,
    }
    # Limpia None para no sobreescribir vacío si no llegó
    vals = {k: v for k, v in vals.items() if v is not None}

    log.info({"event": "odoo_write_multi_attempt", "partner_id": partner_id, "vals": vals})
    ok_write, odoo_response = post_write_multi(partner_id, vals)
    log.info({"event": "odoo_write_multi_result", "partner_id": partner_id, "ok": ok_write})

    return jsonify({
        "ok": bool(ok_write),
        "updated": bool(ok_write),
        "partner_id": partner_id,
        "vals": vals,
        "razon_social": razon_social,
        "sigla": sigla,
        "fecha_matricula": fecha_matricula,
        "ciiu": ciiu,
        "representante_legal": representante_legal,
        "odoo_raw": odoo_response,
    }), 200

@app.get("/health")
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    # En prod usa Gunicorn
    app.run(host="0.0.0.0", port=5000, debug=False)
