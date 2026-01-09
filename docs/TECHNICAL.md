

---



```md

<!-- docs/TECHNICAL.md -->



\# Documentación técnica — Webhook RUES / datos.gov.co → Odoo (res.partner)



\*\*Propósito:\*\* describir arquitectura, reglas de negocio, contratos de integración, configuración y operación del servicio. :contentReference\[oaicite:31]{index=31}  



---



\## 1) Alcance



\*\*Objetivo:\*\* enriquecer terceros en Odoo a partir del NIT. :contentReference\[oaicite:32]{index=32}  



\*\*Incluye:\*\* webhook HTTP, consultas a fuentes públicas, escritura en Odoo vía JSON-RPC. :contentReference\[oaicite:33]{index=33}  



\*\*Fuera de alcance (pendiente):\*\* autenticación, WAF/IP allowlist, colas avanzadas. :contentReference\[oaicite:34]{index=34}  



---



\## 2) Arquitectura lógica



Flujo general: :contentReference\[oaicite:35]{index=35}  

\- Entrada: Odoo → `POST /webhook`  

\- Proceso: normalización NIT + consultas (Socrata → RUES API → fallback HTML) :contentReference\[oaicite:36]{index=36}  

\- Salida: JSON-RPC `res.partner.write` :contentReference\[oaicite:37]{index=37}  



---



\## 3) Contratos de integración (API)



\### 3.1 Endpoints

\- `POST /webhook` :contentReference\[oaicite:38]{index=38}  

\- `GET /health` :contentReference\[oaicite:39]{index=39}  



\### 3.2 Payload mínimo

`id` + `vat` (o `nit`). :contentReference\[oaicite:40]{index=40}  



```json

{

&nbsp; "id": 123,

&nbsp; "vat": "900123456-7"

}



