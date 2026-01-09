# Documentación técnica

Webhook de enriquecimiento RUES / datos.gov.co → Odoo (res.partner)

Versión: 1.1   |   Fecha: 06-01-2026

Este documento describe la arquitectura, lógica de negocio, contratos de integración, configuración y consideraciones operativas del servicio web que:

## 1 Recibe un payload desde Odoo (Webhook/Acción automatizada).

## 2 Consulta datos públicos (datos.gov.co / RUES) usando el NIT.

## 3 Actualiza el tercero (res.partner) en Odoo con campos de identificación y notas de “Representación legal”.

## 1 Alcance y objetivo

Objetivo: automatizar el enriquecimiento de datos de terceros (empresas) en Odoo a partir del NIT, reduciendo la captura manual y mejorando la calidad de la información.

Alcance funcional:

- Endpoint HTTP para recibir el evento (webhook).

- Búsqueda de información del tercero en fuentes públicas.

- Escritura de campos en Odoo en el modelo res.partner vía JSON-RPC.

Fuera de alcance (no implementado en el código):

- Autenticación del webhook (token/firma).

- Control de acceso por IP / WAF (se recomienda implementarlo a nivel de infraestructura).

- Retries avanzados / colas (ej. Celery, RabbitMQ) para alto volumen.

## 2 Repositorio y estructura de archivos

## 3 Arquitectura lógica

Componentes y flujo general:

## 4 Contratos de integración (API)

4.1 Endpoints

4.2 Payload esperado (mínimo)

Campos mínimos requeridos:

Ejemplo (mínimo):

4.3 Extracción del partner_id

El servicio intenta obtener el identificador del tercero desde varias claves del payload, priorizando:

- id

- _id

- record_id

- res_id

- partner_id

Luego convierte el valor a entero; si no existe o no es convertible, retorna 400.

4.4 Normalización del NIT

Reglas:

- Se eliminan todos los caracteres no numéricos.

- Si el resultado tiene longitud >= 9, se toma el “NIT base” removiendo el último dígito (asumiendo que es DV).

Implicación: si el NIT viene sin DV y tiene longitud >= 9, el algoritmo podría recortar un dígito. En operación es recomendable enviar el NIT con DV de forma consistente (ej. 900123456-7) y validar el comportamiento con casos reales.

## 5 Fuentes externas y reglas de consulta

5.1 Socrata (datos.gov.co)

Función: obtener datos base e insumos para construir el identificador de matrícula mercantil (cámara + matrícula).

Consulta típica:

- select: nit, razon_social, sigla, codigo_camara, matricula

- where: nit = '<nit_base>'

- order: matricula DESC (toma el primer resultado)

5.2 RUES API (detalle RM)

Condición de uso: solo se consulta si Socrata retorna codigo_camara y matricula.

Se construye id_rm:

- codigo_camara con padding a 2 dígitos

- matricula con padding a 10 dígitos

- concatenación: id_rm = cc(2) + matrícula(10)

Se intentan dos endpoints (fallback):

- https://www.rues.org.co/api/consultasRUES/consultas/detalleRM?idRM=<id_rm>

- https://www.rues.org.co/api/consultasRUES/detalleRM?idRM=<id_rm>

5.3 RUES Web (HTML) - fallback

Se usa cuando:

- No se logra extraer CIIU desde la respuesta JSON, o

- No se detecta el bloque de “Representación legal”.

Estrategia:

- Construye/usa un web_id (si está disponible en el JSON).

- Realiza scraping a la página de detalle.

- Busca el bloque “Representación legal” y lo guarda como HTML en Odoo (campo comment).

## 6 Transformaciones y mapeo a Odoo

6.1 Campos escritos en res.partner

6.2 Escritura en Odoo (JSON-RPC)

El cliente JSON-RPC usa execute_kw:

- res.partner.read: validación opcional de existencia.

- res.partner.write: escritura de múltiples campos en una sola llamada.

Antes de escribir, se filtran llaves con valor None para evitar sobrescribir datos existentes con vacío.

## 7 Configuración (variables de entorno)

7.1 Variables requeridas (Odoo)

7.2 Variables opcionales (fuentes externas y campos)

## 8 Dependencias

Dependencias usadas por el código:

- Flask (servidor web)

- requests (HTTP)

- python-dotenv (carga .env)

- beautifulsoup4 (scraping HTML)

- re / logging (estándar)

Nota importante:

- El requirements.txt provisto incluye fastapi y uvicorn, pero el servidor implementado es Flask. Se recomienda alinear el archivo de dependencias con la implementación real (agregar flask y eliminar lo no usado, o migrar el servidor a FastAPI si era la intención).

## 9 Manejo de errores y respuestas

## 10 Logging, monitoreo y operación

Logging:

- Se configura logging a nivel INFO con formato: timestamp + nivel + mensaje.

- Se registran eventos clave: payload recibido, consultas a fuentes, valores escritos, errores.

Monitoreo recomendado:

- Healthcheck periódico a /health.

- Alertas por tasa de errores 4xx/5xx.

- Métrica de latencia (tiempo promedio por request) y timeouts externos.

- Rotación de logs a nivel de sistema (logrotate) si se escribe a archivo.

## 11 Despliegue (referencia)

El código incluye app.run() para ejecución directa en desarrollo. Para producción se recomienda:

- Ejecutar detrás de un servidor WSGI (ej. gunicorn) y un proxy reverso (Nginx).

- Habilitar HTTPS (TLS).

- Restringir acceso al endpoint /webhook (token + allowlist de IP si aplica).

Ejemplo conceptual (no incluido en el repo):

- gunicorn -w 2 -b 0.0.0.0:5000 rues_scraper:app

- Nginx: proxy_pass a 127.0.0.1:5000

## 12 Seguridad (brechas actuales y acciones mínimas)

Estado actual:

- No hay autenticación del endpoint /webhook.

- No hay validación de firma del emisor.

Acciones mínimas recomendadas:

## 1 Exigir un token compartido (header X-Webhook-Token) y validarlo.

## 2 Restringir por IP a nivel Nginx/firewall.

## 3 Habilitar rate limiting.

## 4 Manejar datos sensibles: no loguear credenciales ni payloads con información sensible.

## 13 Limitaciones conocidas

## 1 Normalización del NIT:

- La regla de quitar el último dígito si la longitud >= 9 asume DV siempre presente. Esto debe validarse con el comportamiento real de los NIT que llegan desde Odoo.

## 2 Dependencias:

- requirements.txt no refleja exactamente el stack (Flask). Debe corregirse para evitar fallos de instalación.

## 3 Scraping HTML:

- Depende de la estructura del sitio RUES; cambios en el HTML pueden romper el parser.

## 14 Plan de pruebas (mínimo)

Casos sugeridos para validación técnica:


---

# Tablas


## Tabla 1


| Archivo | Responsabilidad principal |

| --- | --- |

| rues_scraper.py (webhook_server.py) | Servidor Flask, lógica del webhook y scraping/consultas externas. |

| odoo_rpc.py | Cliente JSON-RPC para leer/escribir res.partner en Odoo. |

| requirements.txt | Dependencias declaradas (ver notas de consistencia). |


## Tabla 2


| Odoo (Automated Action / Webhook)<br>        |<br>        |  HTTP POST /webhook  (JSON con id + nit/vat)<br>        v<br>Servicio Web (Flask)<br>  - Normaliza NIT<br>  - Consulta Socrata (datos.gov.co)<br>  - Consulta RUES API (detalle RM) si hay cámara+matrícula<br>  - Fallback: scraping HTML RUES (si falta CIIU o Representación legal)<br>        |<br>        |  JSON-RPC (execute_kw: res.partner.write)<br>        v<br>Odoo (res.partner actualizado) |

| --- |


## Tabla 3


| Método | Ruta | Descripción | Respuesta |

| --- | --- | --- | --- |

| POST | /webhook | Recibe payload, consulta fuentes y actualiza res.partner. | 200/400/404/500 |

| GET | /health | Verificación simple de salud del servicio. | 200 {"status":"ok"} |


## Tabla 4


| Campo | Tipo | Descripción | Obligatorio |

| --- | --- | --- | --- |

| id | int | ID del registro res.partner a actualizar. | Sí |

| vat (o nit) | string | NIT del tercero. Se normaliza removiendo caracteres no numéricos. | Sí |


## Tabla 5


| {<br>  "id": 123,<br>  "vat": "900123456-7"<br>} |

| --- |


## Tabla 6


| Campo Odoo | Origen | Regla / comentario |

| --- | --- | --- |

| name | RUES/Socrata | Razón social. |

| x_studio_nombre_comercial | RUES/Socrata | Nombre comercial / sigla. |

| x_studio_fecha_de_matricula | RUES | Fecha de matrícula (ISO YYYY-MM-DD). |

| x_studio_ciiu | RUES/HTML | CIIU (preferible 4 dígitos). |

| comment | RUES HTML | Se guarda HTML de “Representación legal” y/o datos de cámara. |

| (configurable) ODOO_FIELD_CAMARA | Socrata | Nombre cámara (si se define en .env). |

| (configurable) ODOO_FIELD_COD_CAMARA | Socrata | Código cámara (si se define en .env). |


## Tabla 7


| Variable | Ejemplo | Obligatoria | Descripción |

| --- | --- | --- | --- |

| ODOO_JSONRPC | https://tudominio.odoo.com/jsonrpc | Sí | Endpoint JSON-RPC de Odoo. |

| DB | wondertechsas | Sí | Nombre de base de datos. |

| UID | 7 | Sí | ID del usuario técnico. |

| PWD | <api_key o password> | Sí | Credencial del usuario. |


## Tabla 8


| Variable | Default | Descripción |

| --- | --- | --- |

| SOCRATA_URL | https://www.datos.gov.co/resource/c82u-588k.json | Endpoint Socrata. |

| SOCRATA_APP_TOKEN | (vacío) | X-App-Token para cuotas/rate limits. |

| RUES_DETALLE_URL | (vacío) | Sobrescribe el primer endpoint de RUES_DETALLE_URLS. |

| RUES_BASE_WEB | https://www.rues.org.co | Base para scraping HTML. |

| RUES_USER_AGENT | Mozilla/5.0 ... | User-Agent para solicitudes web. |

| TIMEOUT | 12 | Timeout (segundos) para HTTP externo. |

| ODOO_FIELD_CAMARA | (vacío) | Campo custom Odoo para cámara. |

| ODOO_FIELD_COD_CAMARA | (vacío) | Campo custom Odoo para código cámara. |


## Tabla 9


| HTTP | Código interno | Causa típica | Acción recomendada |

| --- | --- | --- | --- |

| 200 | ok | Actualización exitosa. | Verificar vals y cambios en Odoo. |

| 400 | missing_partner_id | No llegó id del registro. | Ajustar body del webhook desde Odoo. |

| 400 | missing_nit | No llegó vat/nit. | Enviar vat (NIT) en el payload. |

| 404 | partner_not_found | El partner_id no existe en Odoo (read falla). | Validar id y permisos del UID. |

| 404 | not_found | Fuentes no retornaron detalle para ese NIT. | Validar NIT y consistencia con DV. |

| 500 | error | Excepción no controlada. | Revisar logs del servicio y respuesta de Odoo. |


## Tabla 10


| Caso | Input (ejemplo) | Resultado esperado |

| --- | --- | --- |

| Éxito con NIT con DV | {id: 1, vat: 900123456-7} | 200 ok y vals con name/ciiu/comment según disponibilidad. |

| Falta id | {vat: 900123456-7} | 400 missing_partner_id. |

| Falta NIT | {id: 1} | 400 missing_nit. |

| Partner inexistente | {id: 999999, vat: 900123456-7} | 404 partner_not_found. |

| NIT no encontrado | {id: 1, vat: 000000000-0} | 404 not_found. |

| Timeout externo simulado | TIMEOUT=1 y NIT válido | 500 error o manejo de excepción; revisar resiliencia. |
