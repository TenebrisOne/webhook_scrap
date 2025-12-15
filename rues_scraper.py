# webhook_scrap.py
import os
import re
import logging
from typing import Optional, Dict, Any, List, Tuple

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# ========= LOG A CONSOLA =========
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
for h in logging.root.handlers[:]:
    logging.root.removeHandler(h)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("rues_odoo")

# ========= ODOO RPC (conservado) =========
from odoo_rpc import post_write, read_fields  # NO TOCAR

# ========= CONFIG =========
SOCRATA_URL = os.getenv("SOCRATA_URL", "https://www.datos.gov.co/resource/c82u-588k.json")
SOCRATA_APP_TOKEN = os.getenv("SOCRATA_APP_TOKEN")  # opcional

# Probamos varias rutas de detalle RUES (algunas cámaras responden por WEB2, otras por WEB)
RUES_DETALLE_URLS = [
    os.getenv("RUES_DETALLE_URL", "https://ruesapi.rues.org.co/WEB2/api/Expediente/DetalleRM/{}"),
    "https://ruesapi.rues.org.co/WEB/api/Expediente/DetalleRM/{}",
]
RUES_BASE_WEB = os.getenv("RUES_BASE_WEB", "https://www.rues.org.co")

TIMEOUT = int(os.getenv("TIMEOUT", "12"))
RUES_UA = os.getenv("RUES_USER_AGENT", "Mozilla/5.0 (RUES-Scraper/1.0)")

# Campo de sigla en Odoo
ODOO_SIGLA_FIELD = os.getenv("ODOO_SIGLA_FIELD", "x_sigla")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))

# ========= APP =========
app = FastAPI(title="RUES + Odoo Webhook", version="2.9.0")

# ========= MODELOS =========
class WebhookIn(BaseModel):
    nit: Optional[str] = None
    vat: Optional[Any] = None
    id: Optional[int] = None
    _action: Optional[str] = None
    _id: Optional[int] = None
    _model: Optional[str] = None
    model_config = ConfigDict(extra="allow")

class WebhookOut(BaseModel):
    razon_social: Optional[str] = None
    sigla: Optional[str] = None
    fecha_matricula: Optional[str] = None       # YYYY-MM-DD
    ciiu: Optional[str] = None                  # primer código CIIU
    representante_legal: Optional[str] = None   # nombres concatenados

class OdooWebhookIn(BaseModel):
    id: int
    nit: Optional[str] = None
    vat: Optional[Any] = None
    model_config = ConfigDict(extra="allow")

# ========= HELPERS NIT =========
def only_digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")

def nit_base_sin_dv(s: str) -> str:
    digits = only_digits(s)
    return digits[:-1] if len(digits) >= 9 else digits

def extract_nit_from_payload(nit: Optional[str], vat: Optional[Any]) -> Optional[str]:
    if nit and isinstance(nit, str) and nit.strip():
        return nit.strip()
    if vat is None or vat is False:
        return None
    if isinstance(vat, (int, float)):
        return str(int(vat))
    if isinstance(vat, str) and vat.strip():
        return vat.strip()
    return None

# ========= UTILS FECHAS / CAMPOS =========
def _to_iso_date(value: Any) -> Optional[str]:
    if not value:
        return None
    s = str(value).strip()
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
    sigla = _first_nonempty_str(
        detalle.get("sigla"),
        empresa.get("sigla") if empresa else None,
    )
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

    # CIIU
    ciiu_code: Optional[str] = None
    posibles_listas = [
        detalle.get("actividadesEconomicas"),
        detalle.get("actividades"),
        detalle.get("actividadEconomica"),
    ]
    if not any(posibles_listas) and isinstance(detalle.get("empresa"), dict):
        emp = detalle["empresa"]
        posibles_listas = [emp.get("actividadesEconomicas"), emp.get("actividades"), emp.get("actividadEconomica")]
    for lst in posibles_listas:
        if isinstance(lst, list) and lst:
            item0 = lst[0]
            if isinstance(item0, dict):
                ciiu_code = _first_nonempty_str(
                    item0.get("codigoCIIU"), item0.get("ciiu"), item0.get("codigo"), item0.get("codigoCiiu")
                )
                if ciiu_code:
                    break
        elif isinstance(lst, dict):
            ciiu_code = _first_nonempty_str(
                lst.get("codigoCIIU"), lst.get("ciiu"), lst.get("codigo"), lst.get("codigoCiiu")
            )
            if ciiu_code:
                break

    # Representante legal
    rep_legal: Optional[str] = None
    candidatos_reps = [
        detalle.get("representantesLegales"),
        detalle.get("representantes"),
        detalle.get("apoderados"),
        detalle.get("junta"),
        detalle.get("personas"),
    ]
    if all(not x for x in candidatos_reps) and isinstance(detalle.get("empresa"), dict):
        emp = detalle["empresa"]
        candidatos_reps = [emp.get("representantesLegales"), emp.get("representantes"), emp.get("apoderados"), emp.get("junta"), emp.get("personas")]
    nombres: List[str] = []
    for bloque in candidatos_reps:
        if isinstance(bloque, list):
            for p in bloque:
                if not isinstance(p, dict):
                    continue
                nombre = _first_nonempty_str(p.get("nombre"), p.get("nombreCompleto"), p.get("razonSocial"), p.get("nombres"))
                rol = _first_nonempty_str(p.get("rol"), p.get("cargo"), p.get("tipo"))
                if rol and "representante" in rol.lower():
                    if nombre:
                        nombres.append(nombre)
                elif not nombres and nombre:
                    nombres.append(nombre)
        elif isinstance(bloque, dict):
            nombre = _first_nonempty_str(bloque.get("nombre"), bloque.get("nombreCompleto"), bloque.get("razonSocial"), bloque.get("nombres"))
            if nombre:
                nombres.append(nombre)
    nombres = [n for n in dict.fromkeys([n.strip() for n in nombres if isinstance(n, str) and n.strip()])]
    if nombres:
        rep_legal = ", ".join(nombres[:2])

    return {"fecha_matricula": fecha_iso, "ciiu": ciiu_code, "representante_legal": rep_legal}

# ========= HELPERS RECURSIVOS (CIIU / REPRESENTANTE) =========
def _iter_kv(obj):
    """Itera (key_path, key, value) para dicts/listas anidados."""
    def _recur(cur, path):
        if isinstance(cur, dict):
            for k, v in cur.items():
                p = (*path, k)
                yield p, k, v
                yield from _recur(v, p)
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                p = (*path, f"[{i}]")
                yield from _recur(v, p)
    yield from _recur(obj, ())

_CIIU_KEY_RE = re.compile(r"(ciiu|actividad|codigo.*ciiu)", re.I)
_CIIU_VAL_RE = re.compile(r"\b(\d{4})\b")
_REP_KEY_RE = re.compile(r"represent", re.I)
_NAME_KEYS = ("nombreCompleto", "nombre", "razonSocial", "nombres")

def find_first_ciiu_anywhere(registro: Dict[str, Any]) -> Optional[str]:
    # 1) por claves ciiu/actividad
    for path, k, v in _iter_kv(registro):
        if isinstance(v, str) and _CIIU_KEY_RE.search(k or ""):
            m = _CIIU_VAL_RE.search(v)
            if m:
                return m.group(1)
        if isinstance(v, dict) and _CIIU_KEY_RE.search(k or ""):
            for _, kk, vv in _iter_kv(v):
                if isinstance(vv, str):
                    m = _CIIU_VAL_RE.search(vv)
                    if m:
                        return m.group(1)
        if isinstance(v, list) and _CIIU_KEY_RE.search(k or ""):
            for item in v:
                if isinstance(item, dict):
                    for _, kk, vv in _iter_kv(item):
                        if isinstance(vv, str):
                            m = _CIIU_VAL_RE.search(vv)
                            if m:
                                return m.group(1)
                elif isinstance(item, str):
                    m = _CIIU_VAL_RE.search(item)
                    if m:
                        return m.group(1)
    # 2) primer 4 dígitos global
    for _, _, v in _iter_kv(registro):
        if isinstance(v, str):
            m = _CIIU_VAL_RE.search(v)
            if m:
                return m.group(1)
    return None

def _extract_name_from_person(d: Dict[str, Any]) -> Optional[str]:
    for key in _NAME_KEYS:
        val = d.get(key)
        if isinstance(val, str) and val.strip():
            return re.sub(r"\s+", " ", val.strip())
    # a veces viene "doc - NOMBRE"
    for _, _, v in _iter_kv(d):
        if isinstance(v, str) and "-" in v:
            m = re.search(r"-\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s\.]+)$", v.strip())
            if m:
                return re.sub(r"\s+", " ", m.group(1)).strip()
    return None

def find_first_representante_anywhere(registro: Dict[str, Any]) -> Optional[str]:
    candidatos_principales: List[str] = []
    candidatos_generales: List[str] = []
    for path, k, v in _iter_kv(registro):
        if _REP_KEY_RE.search(k or ""):
            if isinstance(v, list):
                for person in v:
                    if not isinstance(person, dict):
                        continue
                    name = _extract_name_from_person(person)
                    if not name:
                        continue
                    rol = None
                    for rk in ("rol", "cargo", "tipo"):
                        rv = person.get(rk)
                        if isinstance(rv, str) and rv.strip():
                            rol = rv.lower()
                            break
                    if rol and "principal" in rol:
                        candidatos_principales.append(name)
                    else:
                        candidatos_generales.append(name)
            elif isinstance(v, dict):
                name = _extract_name_from_person(v)
                if name:
                    candidatos_generales.append(name)
    if candidatos_principales:
        return candidatos_principales[0]
    if candidatos_generales:
        return candidatos_generales[0]
    return None

# ========= HELPER ROBUSTO: REPRESENTANTE LEGAL DESDE HTML =========
def _extract_representante_from_soup(soup: BeautifulSoup) -> Optional[str]:
    """
    Busca el primer Representante legal visible:
      - Prioriza bloque 'PRINCIPALES' si existe
      - Soporta tablas, listas y líneas 'doc - NOMBRE'
      - Devuelve el primer nombre con pinta de nombre propio en MAYÚSCULAS
    """
    header = soup.find(string=re.compile(r"^\s*Representaci[oó]n?\s+legal\s*$", re.I)) \
          or soup.find(string=re.compile(r"^\s*Representante\s+legal\s*$", re.I))
    container = None
    if header:
        cand = header.parent if hasattr(header, "parent") else None
        for _ in range(6):
            if not cand: break
            if getattr(cand, "name", None) in ("section", "div", "article", "main"):
                container = cand
            cand = getattr(cand, "parent", None)

    scope = container if container else soup

    def clean(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip()

    text_scope = scope.get_text("\n", strip=True)
    m = re.search(r"PRINCIPALES(.*?)(?:SUPLENTES|SEGUNDOS|$)", text_scope, re.I | re.S)
    principales_block = m.group(1) if m else None

    def pick_name_from_text(t: str) -> Optional[str]:
        m1 = re.search(r"-\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s\.'-]{3,})$", t.strip(), re.M)
        if m1:
            return clean(m1.group(1))
        candidates = re.findall(r"\b([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ\s\.'-]{3,})\b", t)
        BAD = {"PRINCIPALES","SUPLENTES","REPRESENTANTE","LEGAL","REPRESENTACIÓN","DOCUMENTO","NÚMERO","TIPO","CARGO"}
        for c in candidates:
            words = {w.strip(".") for w in c.split()}
            if words.isdisjoint(BAD) and len(c.strip()) >= 6:
                return clean(c)
        return None

    if principales_block:
        name = pick_name_from_text(principales_block)
        if name:
            return name

    for table in scope.find_all("table"):
        for tr in table.find_all("tr"):
            tds = [clean(td.get_text(" ", strip=True)) for td in tr.find_all(["td","th"])]
            if not tds:
                continue
            row_txt = " - ".join(tds)
            name = pick_name_from_text(row_txt)
            if not name and len(tds) >= 2:
                name = pick_name_from_text(tds[-1]) or pick_name_from_text(tds[1])
            if name:
                return name

    for li in scope.find_all(["li","p","div","span"]):
        txt = clean(li.get_text(" ", strip=True))
        if not txt:
            continue
        name = pick_name_from_text(txt)
        if name:
            return name

    return pick_name_from_text(text_scope)

# ========= RUES / DATOS ABIERTOS =========
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
            if isinstance(reg, dict):
                log.info({"event": "rues_detalle_unwrap", "keys": list(reg.keys())[:15]})
                return reg
        if isinstance(regs, dict) and regs:
            log.info({"event": "rues_detalle_unwrap", "keys": list(regs.keys())[:15]})
            return regs
    if "registro" in js and isinstance(js["registro"], dict):
        reg = js["registro"]
        log.info({"event": "rues_detalle_unwrap", "keys": list(reg.keys())[:15]})
        return reg
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
            top_keys = list(js.keys())[:15] if isinstance(js, dict) else []
            log.info({"event": "rues_detalle_keys", "url": url, "top_keys": top_keys})
            reg = unwrap_rues_registro(js)
            if isinstance(reg, dict) and reg:
                return reg
        except Exception as e:
            log.warning({"event": "rues_detalle_error", "url": url, "error": str(e)})
    return {}

# ========= FALLBACK HTML (buscar -> detalle) =========
def fetch_detail_from_html(nit_base: str) -> Dict[str, Optional[str]]:
    """
    1) GET /buscar/RM/{nit}
    2) Tomar primer <a href="/detalle/{id}">Ver información</a>
    3) Abrir el detalle y raspar:
       - razon_social, sigla, fecha_matricula
       - ciiu (PRIMER código de la pestaña 'Actividad económica')
       - representante_legal (PRIMER 'principal' si existe)
    """
    session = requests.Session()
    session.headers.update({"User-Agent": RUES_UA})

    search_url = f"{RUES_BASE_WEB}/buscar/RM/{nit_base}"
    r = session.get(search_url, timeout=TIMEOUT)
    log.info({"event": "html_search_http", "url": search_url, "status": r.status_code})
    if r.status_code != 200:
        return {}

    soup = BeautifulSoup(r.text, "html.parser")

    razon_social = None
    title = soup.select_one("p.font-rues-large.filtro__titulo")
    if title:
        razon_social = title.get_text(strip=True)

    # link "Ver información"
    detail_href = None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        txt = (a.get_text() or "").strip().lower()
        if "/detalle/" in href or "ver información" in txt or "ver informacion" in txt:
            detail_href = href
            break
    if not detail_href:
        log.warning({"event": "html_detail_link_not_found"})
        return {"razon_social": razon_social}

    detail_url = urljoin(RUES_BASE_WEB, detail_href)
    r2 = session.get(detail_url, timeout=TIMEOUT)
    log.info({"event": "html_detail_http", "url": detail_url, "status": r2.status_code})
    if r2.status_code != 200:
        return {"razon_social": razon_social}

    s2 = BeautifulSoup(r2.text, "html.parser")

    def find_value_by_label(label_regex: str) -> Optional[str]:
        lbl = s2.find(string=re.compile(label_regex, re.I))
        if not lbl:
            return None
        node = lbl.parent if hasattr(lbl, "parent") else None
        if node:
            for sib in node.next_elements:
                if isinstance(sib, str):
                    val = sib.strip()
                    if val and not re.search(label_regex, val, re.I):
                        return re.sub(r"\s+", " ", val)
        return None

    # razon social (fallback adicional en el detalle)
    name_detail = None
    for sel in ["h1", "h2", "p.font-rues-large.filtro__titulo"]:
        el = s2.select_one(sel)
        if el and el.get_text(strip=True):
            name_detail = el.get_text(strip=True)
            break
    razon_social = razon_social or name_detail

    # sigla
    sigla = find_value_by_label(r"^\s*sigla\s*$") or find_value_by_label(r"sigla")

    # fecha de matrícula / inscripción / constitución
    fecha = (
        find_value_by_label(r"fecha\s+de\s+matr[íi]cula")
        or find_value_by_label(r"fecha\s+de\s+inscripci[óo]n")
        or find_value_by_label(r"fecha\s+de\s+constituci[óo]n")
    )
    fecha_iso = _to_iso_date(fecha)

    # CIIU (primer código de 4 dígitos en pestaña Actividad económica)
    ciiu = None
    act_label = s2.find(string=re.compile(r"^\s*Actividad\s+econ[oó]mica\s*$", re.I))
    act_container = None
    if act_label:
        cand = act_label.parent
        for _ in range(4):
            if not cand:
                break
            if cand.name in ("section", "div", "article"):
                act_container = cand
            cand = cand.parent
    if act_container:
        a_code = act_container.find("a", string=re.compile(r"^\s*\d{4}\s*$"))
        if a_code:
            ciiu = re.findall(r"\d{4}", a_code.get_text())[:1]
            ciiu = ciiu[0] if ciiu else None
        if not ciiu:
            m = re.search(r"\b(\d{4})\b", act_container.get_text(" ", strip=True))
            if m:
                ciiu = m.group(1)
    if not ciiu:
        a_code = s2.find("a", string=re.compile(r"^\s*\d{4}\s*$"))
        if a_code:
            ciiu = re.findall(r"\d{4}", a_code.get_text())[:1]
            ciiu = ciiu[0] if ciiu else None
    if not ciiu:
        m = re.search(r"\b(\d{4})\b", s2.get_text(" ", strip=True))
        if m:
            ciiu = m.group(1)

    # Representante legal – usa helper robusto
    representante = _extract_representante_from_soup(s2)

    parsed = {
        "razon_social": razon_social,
        "sigla": sigla,
        "fecha_matricula": fecha_iso,
        "ciiu": ciiu,
        "representante_legal": representante,
    }
    log.info({"event": "html_detail_parsed", "parsed": {k: v for k, v in parsed.items() if v}})
    return parsed

# ========= DETALLE DIRECTO POR ID WEB =========
def fetch_detail_from_web_id(web_id: Any) -> Dict[str, Optional[str]]:
    """
    Carga directamente https://www.rues.org.co/detalle/{id}/ y extrae:
      - razon_social, sigla, fecha_matricula, ciiu, representante_legal
    """
    try:
        did = int(str(web_id).strip())
    except Exception:
        log.warning({"event": "web_id_invalid", "web_id": web_id})
        return {}

    url = f"{RUES_BASE_WEB}/detalle/{did}/"
    session = requests.Session()
    session.headers.update({"User-Agent": RUES_UA})

    r = session.get(url, timeout=TIMEOUT)
    log.info({"event": "html_detail_by_id_http", "url": url, "status": r.status_code})
    if r.status_code != 200 or not r.text:
        return {}

    s2 = BeautifulSoup(r.text, "html.parser")

    def find_value_by_label(label_regex: str) -> Optional[str]:
        lbl = s2.find(string=re.compile(label_regex, re.I))
        if not lbl:
            return None
        node = lbl.parent if hasattr(lbl, "parent") else None
        if node:
            for sib in node.next_elements:
                if isinstance(sib, str):
                    val = sib.strip()
                    if val and not re.search(label_regex, val, re.I):
                        return re.sub(r"\s+", " ", val)
        return None

    # razon social
    razon_social = None
    for sel in ["h1", "h2", "p.font-rues-large.filtro__titulo"]:
        el = s2.select_one(sel)
        if el and el.get_text(strip=True):
            razon_social = el.get_text(strip=True)
            break

    # sigla
    sigla = find_value_by_label(r"^\s*sigla\s*$") or find_value_by_label(r"sigla")

    # fecha
    fecha = (
        find_value_by_label(r"fecha\s+de\s+matr[íi]cula")
        or find_value_by_label(r"fecha\s+de\s+inscripci[óo]n")
        or find_value_by_label(r"fecha\s+de\s+constituci[óo]n")
    )
    fecha_iso = _to_iso_date(fecha)

    # CIIU
    ciiu = None
    act_label = s2.find(string=re.compile(r"^\s*Actividad\s+econ[oó]mica\s*$", re.I))
    act_container = None
    if act_label:
        cand = act_label.parent
        for _ in range(4):
            if not cand:
                break
            if cand.name in ("section", "div", "article"):
                act_container = cand
            cand = cand.parent
    if act_container:
        a_code = act_container.find("a", string=re.compile(r"^\s*\d{4}\s*$"))
        if a_code:
            m = re.findall(r"\d{4}", a_code.get_text())
            if m:
                ciiu = m[0]
        if not ciiu:
            m = re.search(r"\b(\d{4})\b", act_container.get_text(" ", strip=True))
            if m:
                ciiu = m.group(1)
    if not ciiu:
        a_code = s2.find("a", string=re.compile(r"^\s*\d{4}\s*$"))
        if a_code:
            m = re.findall(r"\d{4}", a_code.get_text())
            if m:
                ciiu = m[0]
    if not ciiu:
        m = re.search(r"\b(\d{4})\b", s2.get_text(" ", strip=True))
        if m:
            ciiu = m.group(1)

    # Representante legal – helper robusto
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

# ========= ENDPOINTS =========
@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/webhook", response_model=WebhookOut)
def webhook(payload: WebhookIn):
    """
    Recibe {nit} o {vat} y retorna SOLO:
      { "razon_social": "...", "sigla": "...", "fecha_matricula": "...", "ciiu": "...", "representante_legal": "..." }
    """
    raw_nit = extract_nit_from_payload(payload.nit, payload.vat)
    nit_digits = nit_base_sin_dv(raw_nit or "")
    if not nit_digits:
        raise HTTPException(status_code=400, detail="NIT vacío o inválido (revise 'nit' o 'vat')")

    razon_social: Optional[str] = None
    sigla: Optional[str] = None
    fecha_matricula: Optional[str] = None
    ciiu: Optional[str] = None
    representante_legal: Optional[str] = None

    # 1) Datos Abiertos
    try:
        row = fetch_socrata(nit_digits)
    except requests.HTTPError:
        row = None

    # 2) Detalle por API (si se puede)
    detalle: Dict[str, Any] = {}
    if row and row.get("codigo_camara") and row.get("matricula"):
        id_rm = build_id_rm(row["codigo_camara"], row["matricula"])
        log.info({"event": "id_rm_built", "codigo_camara": row.get("codigo_camara"), "matricula": row.get("matricula"), "id_rm": id_rm})
        if id_rm:
            detalle = fetch_rues_detalle_api(id_rm)

    if detalle:
        # nombre/sigla
        name_d, sigla_d = extract_name_sigla(detalle)
        name_d = name_d or (detalle.get("razon_social") or "").strip() or None
        sigla_d = sigla_d or (detalle.get("sigla") or "").strip() or None
        razon_social = (row.get("razon_social") if row else None) or name_d
        sigla = (row.get("sigla") if row else None) or sigla_d

        # extras
        extras = extract_rues_extras(detalle)
        fecha_matricula = extras.get("fecha_matricula")
        ciiu = extras.get("ciiu") or ciiu
        representante_legal = extras.get("representante_legal") or representante_legal

        # completamos por búsqueda recursiva si faltan
        if not ciiu:
            ciiu = find_first_ciiu_anywhere(detalle)
        if not representante_legal:
            representante_legal = find_first_representante_anywhere(detalle)
        log.info({"event": "api_detail_extracted", "ciiu": ciiu, "representante_legal": representante_legal})

        # enriquecer con HTML si aún falta algo: primero /detalle/{id}/
        if not (ciiu and representante_legal):
            web_id = detalle.get("id") or detalle.get("id_detalle") or detalle.get("id_detalle_web")
            if web_id is not None:
                log.info({"event": "api_missing_fields_try_detail_id", "web_id": web_id, "need_ciiu": not ciiu, "need_rep": not representante_legal})
                html_by_id = fetch_detail_from_web_id(web_id)
                if html_by_id:
                    razon_social = razon_social or html_by_id.get("razon_social")
                    sigla = sigla or html_by_id.get("sigla")
                    fecha_matricula = fecha_matricula or html_by_id.get("fecha_matricula")
                    ciiu = ciiu or html_by_id.get("ciiu")
                    representante_legal = representante_legal or html_by_id.get("representante_legal")
                log.info({"event": "enriched_from_detail_id", "ciiu": ciiu, "representante_legal": representante_legal})
            else:
                # si no hay id, último recurso: flujo buscar->detalle
                log.info({"event": "api_missing_fields_try_html", "need_ciiu": not ciiu, "need_rep": not representante_legal})
                html_parsed = fetch_detail_from_html(nit_digits)
                if html_parsed:
                    razon_social = razon_social or html_parsed.get("razon_social")
                    sigla = sigla or html_parsed.get("sigla")
                    fecha_matricula = fecha_matricula or html_parsed.get("fecha_matricula")
                    ciiu = ciiu or html_parsed.get("ciiu")
                    representante_legal = representante_legal or html_parsed.get("representante_legal")
                log.info({"event": "enriched_from_html", "ciiu": ciiu, "representante_legal": representante_legal})

    else:
        # 3) Fallback: HTML (buscar -> ver información)
        log.info({"event": "fallback_html", "nit_base": nit_digits})
        html_parsed = fetch_detail_from_html(nit_digits)
        if html_parsed:
            razon_social = razon_social or (row.get("razon_social") if row else None) or html_parsed.get("razon_social")
            sigla = sigla or (row.get("sigla") if row else None) or html_parsed.get("sigla")
            fecha_matricula = fecha_matricula or html_parsed.get("fecha_matricula")
            ciiu = ciiu or html_parsed.get("ciiu")
            representante_legal = representante_legal or html_parsed.get("representante_legal")

    if not (razon_social or sigla or fecha_matricula or ciiu or representante_legal):
        raise HTTPException(status_code=404, detail="No encontré datos para el NIT enviado")

    return WebhookOut(
        razon_social=razon_social,
        sigla=sigla,
        fecha_matricula=fecha_matricula,
        ciiu=ciiu,
        representante_legal=representante_legal,
    )

@app.post("/webhook/odoo")
def webhook_odoo(payload: OdooWebhookIn):
    partner_id = payload.id
    raw_nit = extract_nit_from_payload(payload.nit, payload.vat)
    nit_digits = nit_base_sin_dv(raw_nit or "")
    if not nit_digits:
        raise HTTPException(status_code=400, detail="NIT vacío o inválido (revise 'nit' o 'vat')")

    try:
        exists, current = read_fields(partner_id, ["id", "name", ODOO_SIGLA_FIELD])
        if not exists:
            raise HTTPException(status_code=404, detail="partner_not_found")
    except Exception as e:
        log.warning(f"No se pudo validar/leer partner en Odoo: {e}")
        current = {}

    razon_social = None
    sigla = None
    fecha_matricula = None
    ciiu = None
    representante_legal = None

    try:
        row = fetch_socrata(nit_digits)
    except requests.HTTPError:
        row = None

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

        if not ciiu:
            ciiu = find_first_ciiu_anywhere(detalle)
        if not representante_legal:
            representante_legal = find_first_representante_anywhere(detalle)
        log.info({"event": "api_detail_extracted", "ciiu": ciiu, "representante_legal": representante_legal})

        # enriquecer con HTML si aún falta algo: primero /detalle/{id}/
        if not (ciiu and representante_legal):
            web_id = detalle.get("id") or detalle.get("id_detalle") or detalle.get("id_detalle_web")
            if web_id is not None:
                log.info({"event": "api_missing_fields_try_detail_id", "web_id": web_id, "need_ciiu": not ciiu, "need_rep": not representante_legal})
                html_by_id = fetch_detail_from_web_id(web_id)
                if html_by_id:
                    razon_social = razon_social or html_by_id.get("razon_social")
                    sigla = sigla or html_by_id.get("sigla")
                    fecha_matricula = fecha_matricula or html_by_id.get("fecha_matricula")
                    ciiu = ciiu or html_by_id.get("ciiu")
                    representante_legal = representante_legal or html_by_id.get("representante_legal")
                log.info({"event": "enriched_from_detail_id", "ciiu": ciiu, "representante_legal": representante_legal})
            else:
                log.info({"event": "api_missing_fields_try_html", "need_ciiu": not ciiu, "need_rep": not representante_legal})
                html_parsed = fetch_detail_from_html(nit_digits)
                if html_parsed:
                    razon_social = razon_social or html_parsed.get("razon_social")
                    sigla = sigla or html_parsed.get("sigla")
                    fecha_matricula = fecha_matricula or html_parsed.get("fecha_matricula")
                    ciiu = ciiu or html_parsed.get("ciiu")
                    representante_legal = representante_legal or html_parsed.get("representante_legal")
                log.info({"event": "enriched_from_html", "ciiu": ciiu, "representante_legal": representante_legal})

    else:
        log.info({"event": "fallback_html", "nit_base": nit_digits})
        html_parsed = fetch_detail_from_html(nit_digits)
        if html_parsed:
            razon_social = razon_social or (row.get("razon_social") if row else None) or html_parsed.get("razon_social")
            sigla = sigla or (row.get("sigla") if row else None) or html_parsed.get("sigla")
            fecha_matricula = fecha_matricula or html_parsed.get("fecha_matricula")
            ciiu = ciiu or html_parsed.get("ciiu")
            representante_legal = representante_legal or html_parsed.get("representante_legal")

    if not (razon_social or sigla or fecha_matricula or ciiu or representante_legal):
        raise HTTPException(status_code=404, detail="No encontré datos para el NIT enviado")

    if isinstance(current, list):
        current = current[0] if current else {}
    current_name = (current or {}).get("name") or ""
    current_sigla = (current or {}).get(ODOO_SIGLA_FIELD) or ""

    vals: Dict[str, Any] = {}
    if razon_social and razon_social != current_name:
        vals["name"] = razon_social
    if sigla and sigla != current_sigla:
        vals[ODOO_SIGLA_FIELD] = sigla

    odoo_result: Dict[str, Any] = {"ok": True, "updated": False, "writes": []}
    if vals:
        try:
            items = list(vals.items())
            ok, resp = post_write(partner_id=int(partner_id), field_name=items[0][0], url_value=items[0][1])
            odoo_result["writes"].append({"field": items[0][0], "value": items[0][1], "ok": ok, "resp": resp})
            updated = ok
            if ok and len(items) > 1:
                for fname, fval in items[1:]:
                    ok2, resp2 = post_write(partner_id=int(partner_id), field_name=fname, url_value=fval)
                    odoo_result["writes"].append({"field": fname, "value": fval, "ok": ok2, "resp": resp2})
                    updated = updated and ok2
            odoo_result["ok"] = updated
            odoo_result["updated"] = True
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error escribiendo en Odoo: {e}")

    return {
        "ok": odoo_result["ok"],
        "updated": odoo_result["updated"],
        "partner_id": partner_id,
        "vals": vals,
        "writes": odoo_result["writes"],
        "razon_social": razon_social,
        "sigla": sigla,
        "fecha_matricula": fecha_matricula,
        "ciiu": ciiu,
        "representante_legal": representante_legal,
    }

# ========= MAIN =========
if __name__ == "__main__":
    import uvicorn
    # usa el nombre real del archivo (webhook_scrap.py)
    uvicorn.run("rues_scraper:app", host=HOST, port=PORT, reload=False)
