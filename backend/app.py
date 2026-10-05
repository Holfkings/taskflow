"""TaskFlow: landing estatica + API de leads en el MISMO origen.

Sirve index.html y monta POST /api/leads, para que el <form action> del
frontend sea un fallback de verdad (sin CORS, sin cold start entre origenes).

Ejecucion local:  python backend/app.py
Produccion:       gunicorn --chdir backend app:app  (PORT inyectado)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request, redirect, send_from_directory, url_for  # noqa: E402

import store  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, static_folder=None)  # las rutas las sirve _send
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024  # un lead son bytes, no uploads

THANK_YOU_FRAGMENT = "#gracias"


_DB_READY = False


@app.before_request
def _ensure_db():
    """Crea el schema UNA vez por worker, no en cada request.

    Antes esto corria executescript() en cada peticion (incluidos los assets
    estaticos): tomaba un write lock de SQLite por request y hacia que /api/health
    dejara de ser sin efectos secundarios. Ahora es idempotente por proceso.
    """
    global _DB_READY
    if _DB_READY:
        return
    try:
        store.init_db()
        _DB_READY = True
    except Exception:  # pragma: no cover
        # La landing es una pagina de marketing: si la DB de leads no existe
        # todavia (disco no montado, path mal configurado), la landing TIENE
        # que seguir sirviendo. Solo /api/leads y /api/stats fallan, que es lo
        # honesto. Un 500 global por un leads.db ausente tumba el portfolio.
        app.logger.exception("init_db fallo; la landing sigue sirviendo")
        _DB_READY = False


def _client_ip() -> str:
    """X-Forwarded-For solo si hay exactamente un proxy (Render pone uno)."""
    fwd = request.headers.get("X-Forwarded-For", "")
    parts = [p.strip() for p in fwd.split(",") if p.strip()]
    if len(parts) == 1:
        return parts[0]
    return request.remote_addr or ""


def _payload() -> dict:
    """Acepta JSON (fetch) y form-encoded (fallback sin JS) con un solo handler."""
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return request.form.to_dict()


def _utm(data: dict) -> dict:
    """Acepta UTMs planas (fallback form-encoded) o anidadas {"utm": {...}}.

    El fetch del frontend manda el objeto anidado; si solo se leyera el
    plano, cada lead de JS se guardaba sin atribucion y /api/stats era
    medio falso sin avisar.
    """
    src = data.get("utm")
    src = src if isinstance(src, dict) else data

    def _v(name: str, cap: int):
        raw = src.get(name)
        if raw is None:  # anidado sin prefijo vs plano con prefijo
            raw = src.get("utm_" + name)
        return (str(raw)[:cap] or None) if raw else None

    return {
        "utm_source": _v("source", 100),
        "utm_medium": _v("medium", 100),
        "utm_campaign": _v("campaign", 150),
    }


def _persist(data: dict):
    """Aplica el contrato: bump_rate primero, honeypot, luego validacion."""
    email = (data.get("email") or "")[:300]
    honeypot = bool((data.get("company") or "").strip())
    with store.connect() as cx:
        ip_hash = store.hash_ip(_client_ip())
        hits = store.bump_rate(cx, ip_hash)
        if honeypot:
            store.block_ip(cx, ip_hash, "honeypot")
            return 200, {"ok": True, "duplicate": True}, hits  # mismo body que el bot espera ver
        if hits > store.MAX_HITS_PER_HOUR:
            return 429, {"ok": False, "error": "rate_limited"}, hits
        if store.is_blocked(cx, ip_hash):
            return 200, {"ok": True, "duplicate": True}, hits
        if not store.valid_email(email):
            return 400, {"ok": False, "error": "invalid_email"}, hits
        _, is_new = store.upsert_lead(
            cx, email, source="landing_cta", ip_hash=ip_hash,
            user_agent=(request.headers.get("User-Agent") or "")[:300] or None,
            utm=_utm(data), honeypot=False,
        )
    return (201 if is_new else 200), {"ok": True, "duplicate": not is_new}, hits


@app.post("/api/leads")
def create_lead():
    status, body, _ = _persist(_payload())
    if request.is_json:
        return jsonify(body), status
    # Fallback sin JS: 303 al thank-you SIEMPRE (tambien con 400/429: el usuario
    # necesita ver la pagina, no un error crudo). El codigo va en la cabecera,
    # no como status del redirect, o Flask lo pisa con 302.
    dest = url_for("index") + THANK_YOU_FRAGMENT
    resp = redirect(dest, code=303)
    resp.status_code = 303
    resp.headers["X-Lead-Status"] = str(status)
    return resp


@app.get("/api/health")
def health():
    """Sin efectos secundarios: UptimeRobot lo puede pedir cada 5 min."""
    try:
        with store.connect() as cx:
            cx.execute("SELECT 1").fetchone()
    except Exception:  # pragma: no cover
        return jsonify({"ok": False}), 503
    return jsonify({"ok": True, "rate_limit": store.MAX_HITS_PER_HOUR}), 200


@app.get("/api/stats")
def stats():
    """Dashboard morning. Detras de auth basico por header."""
    token = os.environ.get("STATS_TOKEN")
    if not token:
        return jsonify({"ok": False, "error": "stats_disabled"}), 404
    if request.headers.get("X-Stats-Token") != token:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return jsonify(store.queue_stats()), 200


@app.get("/")
def index():
    return _send("index.html")


@app.get("/<path:path>")
def static_or_404(path):
    """Sirve assets reales y cae a la landing en rutas limpias (sin hashbang routing
    en la pagina, /precios debe devolver la landing, no un 404 de Flask)."""
    full = os.path.normpath(os.path.join(ROOT, path))
    if not full.startswith(ROOT):
        return _send("index.html")  # ../ traversal fuera del root
    if os.path.isfile(full):
        return _send(path)
    return _send("index.html")


def _send(relpath: str):
    return send_from_directory(ROOT, relpath)


@app.errorhandler(413)
def too_big(_e):
    return jsonify({"ok": False, "error": "payload_too_large"}), 413


@app.errorhandler(500)
def boom(e):
    app.logger.exception("lead error: %s", e)
    return jsonify({"ok": False, "error": "server_error"}), 500


if __name__ == "__main__":
    store.init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)