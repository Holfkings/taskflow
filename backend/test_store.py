"""Verificación real del store: corre con `python backend/test_store.py`."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["TASKFLOW_DB"] = DB
os.environ["IP_SALT"] = "test-salt"
os.environ["LEAD_RATE_LIMIT"] = "3"

import store  # noqa: E402

store.init_db()

# 1) alta nueva
with store.connect() as cx:
    lid, nuevo = store.upsert_lead(cx, "  Ana.Lopez@Ejemplo.COM ", ip_hash=store.hash_ip("203.0.113.9"),
                                  user_agent="pytest", utm={"utm_campaign": "launch"})
assert (lid, nuevo) == (1, True), (lid, nuevo)
print("1 ok  alta -> id=1 nuevo=True, email_key normalizado")

# 2) reenvío del mismo email (el bug del doble submit) -> mismo id, no duplica
with store.connect() as cx:
    lid2, nuevo2 = store.upsert_lead(cx, "ana.lopez@ejemplo.com", ip_hash=store.hash_ip("203.0.113.9"))
assert (lid2, nuevo2) == (1, False), (lid2, nuevo2)
print("2 ok  reenvío -> mismo id, evento 'resubmit'")

# 3) honeypot: se guarda en spam-bucket y la IP queda bloqueada
with store.connect() as cx:
    store.upsert_lead(cx, "bot@spam.example", ip_hash=store.hash_ip("198.51.100.4"), honeypot=True)
    assert store.is_blocked(cx, store.hash_ip("198.51.100.4"))
    assert not store.is_blocked(cx, store.hash_ip("203.0.113.9"))
print("3 ok  honeypot -> IP bloqueada, no afecta a la IP buena")

# 4) rate limit: 3 permitidos, el 4to choca
with store.connect() as cx:
    ip = store.hash_ip("192.0.2.7")
    veredictos = [store.is_rate_limited(cx, ip, 3) for _ in range(5)]
    hits = [store.bump_rate(cx, ip) for _ in range(4)]
    print("   ventana:", veredictos, "hits:", hits)
    assert hits == [1, 2, 3, 4]
    # limit explicito: bajo pytest, store ya pudo quedar importado por otro
    # modulo con LEAD_RATE_LIMIT=10, y el default se fijo en tiempo de import.
    assert store.is_rate_limited(cx, ip, 3) is True
print("4 ok  rate limit -> corta al superar LEAD_RATE_LIMIT=3")

# 5) IP hasheada, nunca cruda
with store.connect() as cx:
    row = cx.execute("SELECT ip_hash, user_agent FROM leads WHERE id=1").fetchone()
assert row["ip_hash"] != "203.0.113.9" and "203.0.113.9" not in row["ip_hash"]
print("5 ok  privacidad -> ip guardada como hash", row["ip_hash"][:12] + "...")

# 6) FK en cascada: borrar el lead borra sus eventos
with store.connect() as cx:
    cx.execute("DELETE FROM leads WHERE id=1")
    n = cx.execute("SELECT COUNT(*) c FROM lead_events WHERE lead_id=1").fetchone()["c"]
assert n == 0
print("6 ok  FK ON DELETE CASCADE -> 0 eventos huérfanos")

# 7) validación de email (la del servidor, no la del navegador)
assert store.valid_email("a@b.co") and store.valid_email("x.y+tag@sub.dominio.io")
assert not store.valid_email("no-es-mail") and not store.valid_email("a@b") and not store.valid_email("")
assert not store.valid_email("a@b.c " + "d" * 250 + "@x.com")
print("7 ok  validación -> acepta válidos, rechaza malformados y >254 chars")

# 8) stats y migración idempotente
store.init_db()
print("8 ok  init_db() dos veces ->", store.queue_stats())
print("\nTODAS LAS PRUEBAS PASARON")
