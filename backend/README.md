# TaskFlow — API de leads

Un solo servicio Flask sirve la landing (`index.html`) **y** expone `POST /api/leads`
bajo el mismo origen. Sin CORS, sin dominio que configurar, y el `<form action="/api/leads">`
del frontend funciona como fallback real aunque el visitante tenga JavaScript desactivado.

## Rutas

| Método | Ruta | Respuesta |
|---|---|---|
| `POST` | `/api/leads` | `201` nuevo · `200 {duplicate:true}` reenvío · `400 invalid_email` · `429 rate_limited` |
| `GET` | `/api/health` | `200 {ok:true}` — sin efectos secundarios, para UptimeRobot |
| `GET` | `/api/stats` | `200` con `X-Stats-Token` correcto · `401` incorrecto · `404` si `STATS_TOKEN` no está definido |

Acepta **JSON** (el `fetch` del navegador) y **form-encoded** (el fallback sin JS) en el
mismo handler. Con form-encoded siempre responde `303` al `#gracias`, incluso en error:
el visitante necesita ver la página, no un error crudo; el código real viaja en la
cabecera `X-Lead-Status` para el log del servidor.

```bash
# Lead nuevo
curl -i -X POST http://127.0.0.1:5000/api/leads \
  -H 'Content-Type: application/json' \
  -d '{"email":"ada@example.com","utm_source":"linkedin","utm_campaign":"octubre"}'

# Fallback sin JS (form-encoded, sigue el redirect)
curl -i -X POST http://127.0.0.1:5000/api/leads \
  -d 'email=ada@example.com&utm_source=newsletter'      # -> 303 Location: /#gracias
```

## Decisiones de seguridad

- **`bump_rate` antes de validar** (pedido de @especialista-en-bases-de-datos): el contador
  se incrementa primero y se rechaza con `429` si supera `LEAD_RATE_LIMIT`. Validar primero
  dejaría los submits maliciosos fuera del contador.
- **Honeypot `company`**: el bot recibe exactamente el mismo `200 {ok:true}` que un humano,
  pero el lead **no se escribe** y su IP queda en `blocked_ips` sin releer el payload.
- **La IP nunca se guarda en claro**: `SHA256(IP_SALT|ip)` truncado a 32 chars.
  Cambia `IP_SALT` en producción — con el valor por defecto los hashes son predecibles.
- **Límite de 8 KB** en el body (`MAX_CONTENT_LENGTH`): un lead son bytes, no un upload.
- **`X-Forwarded-For` con un solo valor**: si hay varios proxies encadenados se ignora y se
  usa `remote_addr`, porque cualquiera puede mandar esa cabecera.
- **UTMs acotados** a 100/100/150 caracteres antes de tocar la base.
- **`/api/stats` cerrado** por header; sin `STATS_TOKEN` en el entorno devuelve `404`
  (no expone ni siquiera la existencia del endpoint).

## Pruebas

```bash
python backend/test_store.py    # capa de datos (de @especialista-en-bases-de-datos)
python backend/test_app.py      # contrato del endpoint, cliente de test de Flask
python backend/test_e2e.py      # HTTP real contra un servidor levantado
```

`test_e2e.py` levanta **werkzeug en local y gunicorn en Linux** (gunicorn importa `fcntl`
y no arranca en Windows), y cada bloque de pruebas usa una `X-Forwarded-For` propia porque
el rate limit es por IP: si todas comparten 127.0.0.1, un bloque sabotea al siguiente.

## Dos bugs que los tests cacharon

1. `Flask(static_folder=ROOT, static_url_path="")` registra su propia ruta comodín, que
   **absorbe** el catch-all propio: `/precios` devolvía 404 en vez de la landing.
   Se resolvió con `static_folder=None` y serving explícito con `send_from_directory`.
2. `return redirect(...), status` no devuelve un 303: Flask pisa el código del redirect
   con 302 y el segundo elemento se descarta. El código real va en `X-Lead-Status`.

## Variables de entorno

`TASKFLOW_DB` · `IP_SALT` · `LEAD_RATE_LIMIT` · `STATS_TOKEN` · `PORT`. Ver `render.yaml`.