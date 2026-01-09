<!-- README.md -->



\# Webhook de enriquecimiento RUES / datos.gov.co → Odoo (res.partner)



\*\*Versión:\*\* 1.1  

\*\*Fecha:\*\* 06-01-2026  

\*\*Estado:\*\* En producción



Servicio web que:

1\) Recibe un payload desde Odoo (Webhook/Acción automatizada). :contentReference\[oaicite:1]{index=1}  

2\) Consulta datos públicos (datos.gov.co / RUES) usando el NIT. :contentReference\[oaicite:2]{index=2}  

3\) Actualiza el tercero (`res.partner`) en Odoo con datos de identificación y notas de “Representación legal”. :contentReference\[oaicite:3]{index=3}  



---



\## Índice

\- \[Objetivo y alcance](#objetivo-y-alcance)

\- \[Arquitectura (vista rápida)](#arquitectura-vista-rápida)

\- \[API (endpoints)](#api-endpoints)

\- \[Configuración](#configuración)

\- \[Ejecución local](#ejecución-local)

\- \[Despliegue](#despliegue)

\- \[Operación y monitoreo](#operación-y-monitoreo)

\- \[Seguridad](#seguridad)

\- \[Documentación adicional](#documentación-adicional)



---



\## Objetivo y alcance



\*\*Objetivo:\*\* automatizar el enriquecimiento de datos de terceros (empresas) en Odoo a partir del NIT, reduciendo captura manual y mejorando calidad de información. :contentReference\[oaicite:4]{index=4}  



\*\*Incluye (alcance funcional):\*\*

\- Endpoint HTTP para recibir evento (webhook). :contentReference\[oaicite:5]{index=5}  

\- Búsqueda del tercero en fuentes públicas. :contentReference\[oaicite:6]{index=6}  

\- Escritura de campos en Odoo vía JSON-RPC (`res.partner`). :contentReference\[oaicite:7]{index=7}  



\*\*Fuera de alcance (pendiente / recomendado):\*\*

\- Autenticación del webhook (token/firma). :contentReference\[oaicite:8]{index=8}  

\- Control de acceso por IP / WAF (a nivel infraestructura). :contentReference\[oaicite:9]{index=9}  

\- Retries avanzados / colas (Celery/RabbitMQ) para alto volumen. :contentReference\[oaicite:10]{index=10}  



---



\## Arquitectura (vista rápida)



Flujo lógico: :contentReference\[oaicite:11]{index=11}  



```text

Odoo (Automated Action / Webhook)

&nbsp;       |

&nbsp;       |  HTTP POST /webhook  (JSON con id + nit/vat)

&nbsp;       v

Servicio Web (Flask)

&nbsp; - Normaliza NIT

&nbsp; - Consulta Socrata (datos.gov.co)

&nbsp; - Consulta RUES API (detalle RM) si hay cámara+matrícula

&nbsp; - Fallback: scraping HTML RUES (si falta CIIU o Representación legal)

&nbsp;       |

&nbsp;       |  JSON-RPC (execute\_kw: res.partner.write)

&nbsp;       v

Odoo (res.partner actualizado)



