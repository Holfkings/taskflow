"""Capa de datos de leads (SQLite) para el endpoint de TaskFlow.

Sin ORM: una conexión por request, transacciones explícitas.
Ver schema.sql para el DDL. Pensado para Flask o FastAPI igual.
"""
import hashlib
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

DB_PATH = os.environ.get("TASKFLOW_DB", "leads.db")
IP_SALT = os.environ.get("IP_SALT", "cambiar-en-produccion")
MAX_HITS_PER_HOUR = int(os.environ.get("LEAD_RATE_LIMIT", "10"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")

SCHEMA = open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8").read()


@contextmanager
def connect():
    d = os.path.dirname(os.path.abspath(DB_PATH))
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)  # p.ej. /var/data en Render: el path no existe al arrancar
    cx = sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA foreign_keys = ON")
    try:
        yield cx
        cx.execute("PRAGMA optimize")
    finally:
        cx.close()


def init_db():
    with connect() as cx:
        cx.executescript(SCHEMA)


def hash_ip(ip: str) -> str:
    return hashlib.sha256((IP_SALT + "|" + (ip or "")).encode()).hexdigest()[:32]


def email_key(email: str) -> str:
    return email.strip().lower()


def valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(EMAIL_RE.match(email.strip()))


def is_rate_limited(cx, ip_hash: str, limit: int = MAX_HITS_PER_HOUR) -> bool:
    """Ventana fija: cuenta 0 al empezar la hora, no un promedio flotante."""
    window = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    key = (f"lead:{ip_hash}", window.strftime("%Y-%m-%dT%H:%M:%SZ"))
    row = cx.execute("SELECT hits FROM rate_limits WHERE bucket=? AND window_from=?", key).fetchone()
    return (row["hits"] if row else 0) >= limit


def bump_rate(cx, ip_hash: str) -> int:
    window = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    cx.execute(
        "INSERT INTO rate_limits (bucket, window_from, hits) VALUES (?,?,1) "
        "ON CONFLICT(bucket, window_from) DO UPDATE SET hits = hits + 1",
        (f"lead:{ip_hash}", window.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    return cx.execute("SELECT hits FROM rate_limits WHERE bucket=? AND window_from=?",
                      (f"lead:{ip_hash}", window.strftime("%Y-%m-%dT%H:%M:%SZ"))).fetchone()["hits"]


def block_ip(cx, ip_hash: str, reason: str):
    cx.execute("INSERT OR IGNORE INTO blocked_ips (ip_hash, reason) VALUES (?,?)", (ip_hash, reason))


def is_blocked(cx, ip_hash: str) -> bool:
    return cx.execute("SELECT 1 FROM blocked_ips WHERE ip_hash=?", (ip_hash,)).fetchone() is not None


def upsert_lead(cx, email: str, source="landing_cta", ip_hash=None, user_agent=None,
                utm=None, honeypot=False) -> tuple[int, bool]:
    """Devuelve (lead_id, es_nuevo). Reenvío del mismo email = update, no duplica."""
    utm = utm or {}
    key = email_key(email)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with cx:  # transacción
        if honeypot:
            block_ip(cx, ip_hash, "honeypot")
        existing = cx.execute("SELECT id FROM leads WHERE email_key=?", (key,)).fetchone()
        if existing:
            lead_id = existing["id"]
            cx.execute("UPDATE leads SET user_agent=COALESCE(?, user_agent) WHERE id=?", (user_agent, lead_id))
            cx.execute("INSERT INTO lead_events (lead_id, event, detail) VALUES (?,?,?)",
                       (lead_id, "resubmit", None))
            return lead_id, False
        cur = cx.execute(
            "INSERT INTO leads (email, email_key, source, utm_source, utm_medium, utm_campaign, "
            "user_agent, ip_hash, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (email.strip(), key, source, utm.get("utm_source"), utm.get("utm_medium"),
             utm.get("utm_campaign"), user_agent, ip_hash, now),
        )
        lead_id = cur.lastrowid
        cx.execute("INSERT INTO lead_events (lead_id, event) VALUES (?,?)", (lead_id, "created"))
        return lead_id, True


def queue_stats(hours: int = 24) -> dict:
    """Lo que revisas en la morning: volumen y здоров del rate limiter."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect() as cx:
        total = cx.execute("SELECT COUNT(*) c FROM leads WHERE created_at >= ?", (since,)).fetchone()["c"]
        uniq = cx.execute("SELECT COUNT(DISTINCT email_key) c FROM leads WHERE created_at >= ?", (since,)).fetchone()["c"]
        top = cx.execute(
            "SELECT COALESCE(utm_campaign,'(directo)') camp, COUNT(*) c FROM leads "
            "WHERE created_at >= ? GROUP BY 1 ORDER BY c DESC LIMIT 5", (since,)).fetchall()
        blocked = cx.execute("SELECT COUNT(*) c FROM blocked_ips").fetchone()["c"]
    return {"ventana_horas": hours, "leads": total, "unicos": uniq, "bloqueadas": blocked,
            "top_campanas": [{"campana": r["camp"], "leads": r["c"]} for r in top]}
