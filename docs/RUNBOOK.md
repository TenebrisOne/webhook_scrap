

---



```md

<!-- docs/RUNBOOK.md -->



\# Runbook (Operación) — Webhook RUES → Odoo



Este documento está orientado a operación: monitoreo, alertas, diagnóstico y respuesta ante incidentes.



---



\## 1) Checklist de salud (diario)



\- \[ ] `/health` responde `200 {"status":"ok"}`. :contentReference\[oaicite:64]{index=64}  

\- \[ ] No hay picos de 4xx/5xx (revisar logs). :contentReference\[oaicite:65]{index=65}  

\- \[ ] Latencia dentro de lo esperado (considerar `TIMEOUT`). :contentReference\[oaicite:66]{index=66}  



---



\## 2) Alarmas recomendadas



\- Error rate 5xx > X% en 5 min. :contentReference\[oaicite:67]{index=67}  

\- Error rate 4xx > X% (especialmente `missing\_nit`, `missing\_partner\_id`). :contentReference\[oaicite:68]{index=68}  

\- Latencia p95 > umbral (posibles caídas en RUES/datos.gov.co). :contentReference\[oaicite:69]{index=69}  



---



\## 3) Troubleshooting rápido



\### 3.1 `400 missing\_partner\_id`

\*\*Causa:\*\* el payload no trae `id` (o no es convertible a entero). :contentReference\[oaicite:70]{index=70}  

\*\*Acción:\*\* revisar acción automatizada/webhook en Odoo y el body enviado.



\### 3.2 `400 missing\_nit`

\*\*Causa:\*\* no llegó `vat/nit`. :contentReference\[oaicite:71]{index=71}  

\*\*Acción:\*\* asegurar que el partner tenga NIT y se envíe en el payload.



\### 3.3 `404 partner\_not\_found`

\*\*Causa:\*\* el `id` no existe o el usuario técnico no tiene permisos. :contentReference\[oaicite:72]{index=72}  

\*\*Acción:\*\* validar `UID`, permisos y existencia del registro.



\### 3.4 `404 not\_found`

\*\*Causa:\*\* fuentes externas no retornan detalle para ese NIT. :contentReference\[oaicite:73]{index=73}  

\*\*Acción:\*\* validar NIT/DV; revisar regla de normalización si aplica. :contentReference\[oaicite:74]{index=74}  



\### 3.5 Cambios en HTML de RUES (scraping roto)

\*\*Causa:\*\* el fallback depende de la estructura del sitio RUES. :contentReference\[oaicite:75]{index=75}  

\*\*Acción:\*\* ajustar selectores/parsing; preferir API cuando sea posible.



---



\## 4) Seguridad mínima (pendiente recomendado)



\- Token compartido `X-Webhook-Token`. :contentReference\[oaicite:76]{index=76}  

\- IP allowlist / rate limiting (Nginx/firewall). :contentReference\[oaicite:77]{index=77}  

\- Evitar logs con credenciales/payload sensible. :contentReference\[oaicite:78]{index=78}  



---



\## 5) Notas operativas importantes



\- Regla NIT: si longitud ≥ 9 se remueve último dígito (asume DV). Si llega sin DV, puede recortar un dígito. :contentReference\[oaicite:79]{index=79}  

\- Si `requirements.txt` no corresponde al stack real (Flask), corregir para evitar fallos de instalación. :contentReference\[oaicite:80]{index=80}  



