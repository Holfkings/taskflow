"""Tests de contrato del endpoint. Corre: python backend/test_app.py

Usa el cliente de test de Flask (sin servidor) y un DB temporal por test.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["TASKFLOW_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ["LEAD_RATE_LIMIT"] = "5"
os.environ["IP_SALT"] = "test-salt"

import store  # noqa: E402
import app as appmod  # noqa: E402

FAILED = []


def check(name, cond, extra=None):  # noqa: ANN001
    extra = "" if extra is None else str(extra)
    print(("  ok  " if cond else "  FAIL") + f" {name}" + (f" -> {extra}" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def main():
    appmod.app.config["TESTING"] = True
    c = appmod.app.test_client()

    # Cada grupo usa una IP propia: el rate limit es por IP y 5/hora, asi que
    # compartir 127.0.0.1 entre grupos hace que un grupo sabotee al siguiente.
    def ip(n):
        return {"X-Forwarded-For": f"10.0.0.{n}"}

    def post(email=None, group=0, **kw):
        return c.post("/api/leads", json={"email": email, **kw.pop("body", {})},
                      headers=ip(group), **kw)

    print("1. health")
    r = c.get("/api/health")
    check("200 + ok", r.status_code == 200 and r.get_json()["ok"] is True, r.status_code)

    print("2. lead nuevo en JSON")
    r = post("  Ada@Example.COM ", group=1)
    body = r.get_json()
    check("201 created", r.status_code == 201, r.status_code)
    check("ok true, duplicate false", body["ok"] and body["duplicate"] is False, body)

    print("3. reenvio = duplicate, no duplica filas")
    r = post("ada@example.com", group=1)
    check("200 duplicate", r.status_code == 200 and r.get_json()["duplicate"] is True, r.status_code)
    with store.connect() as cx:
        n = cx.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
        ev = cx.execute("SELECT COUNT(*) c FROM lead_events WHERE event='resubmit'").fetchone()["c"]
    check("1 sola fila de leads", n == 1, n)
    check("evento resubmit registrado", ev == 1, ev)

    print("4. email invalido (cada caso su propia IP: el limite es 5/hora)")
    for i, bad in enumerate(["", "nope", "a@b", "a b@c.com", "x@.com", None, "a@.com", "@x.com"]):
        r = post(bad, group=20 + i)
        ok = r.status_code == 400 and r.get_json()["error"] == "invalid_email"
        check(f"400 en {bad!r}", ok, r.get_json())
    with store.connect() as cx:
        bad_rows = cx.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    check("ningun email invalido llego a la tabla", bad_rows == 1, bad_rows)

    print("5. honeypot: 200 falso, IP bloqueada, no escribe lead")
    with store.connect() as cx:
        before = cx.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    r = post("bot@spam.com", group=3, body={"company": "x"})
    check("200 con ok true", r.status_code == 200 and r.get_json()["ok"] is True, r.status_code)
    with store.connect() as cx:
        after = cx.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
        blocked = cx.execute("SELECT COUNT(*) c FROM blocked_ips").fetchone()["c"]
    check("no se guardo el lead del bot", after == before, f"{before}->{after}")
    check("IP en blocked_ips", blocked == 1, blocked)
    r = post("otro@spam.com", group=3)
    with store.connect() as cx:
        after2 = cx.execute("SELECT COUNT(*) c FROM leads").fetchone()["c"]
    check("IP bloqueada ni se re-evalua", after2 == after, after2)

    print("6. rate limit: bump antes de validar (orden de la DB)")
    codes = [post(f"rl{i}@example.com", group=4).status_code for i in range(7)]
    check("primeros 201 hasta el limite, luego 429",
          codes[:5] == [201] * 5 and codes[5:] == [429] * 2, codes)
    with store.connect() as cx:
        rl = cx.execute("SELECT COUNT(*) c FROM leads WHERE email_key LIKE 'rl%'").fetchone()["c"]
    check("los 2 excedentes no se guardaron", rl == 5, rl)
    r = post("otro@example.com", group=5)
    check("otra IP no comparte el limite", r.status_code == 201, r.status_code)

    print("7. IP nunca cruda en la DB")
    with store.connect() as cx:
        hashes = [r["ip_hash"] for r in cx.execute("SELECT ip_hash FROM leads").fetchall()]
        hashes += [r["ip_hash"] for r in cx.execute("SELECT ip_hash FROM blocked_ips").fetchall()]
    check("todas son hash de 32 chars", all(len(h) == 32 for h in hashes), hashes)
    check("ninguna IP en texto plano",
          not any(ip in (h or "") for h in hashes for ip in
                  ("10.0.0.1", "10.0.0.3", "10.0.0.4", "127.0.0.1")))

    print("8. fallback sin JS: form-encoded + 303")
    r = c.post("/api/leads", data={"email": "form@example.com",
                                  "utm_source": "linkedin", "utm_campaign": "outreach"},
               headers=ip(6))
    check("303 redirect", r.status_code == 303, r.status_code)
    check("Location con #gracias", "#gracias" in r.headers.get("Location", ""), r.headers.get("Location"))
    with store.connect() as cx:
        row = cx.execute("SELECT utm_source, utm_campaign FROM leads "
                         "WHERE email_key='form@example.com'").fetchone()
    check("UTMs guardados",
          row and row["utm_source"] == "linkedin" and row["utm_campaign"] == "outreach", dict(row or {}))

    print("9. UTM basura se acota a 100 chars")
    r = post("long@example.com", group=7, body={"utm_source": "x" * 500})
    with store.connect() as cx:
        row = cx.execute("SELECT utm_source FROM leads WHERE email_key='long@example.com'").fetchone()
    check("201 + truncado a 100",
          r.status_code == 201 and row and len(row["utm_source"]) == 100,
          f"{r.status_code} {len(row['utm_source']) if row else None}")

    print("9b. UTM anidada (como la manda el fetch del form) se guarda igual")
    r = post("nested@example.com", group=8, body={"utm": {"source": "linkedin",
                                                         "campaign": "outreach",
                                                         "medium": "social"}})
    with store.connect() as cx:
        row = cx.execute("SELECT utm_source, utm_medium, utm_campaign FROM leads "
                         "WHERE email_key='nested@example.com'").fetchone()
    check("201 + UTMs del objeto anidado",
          r.status_code == 201 and row and row["utm_source"] == "linkedin"
          and row["utm_medium"] == "social" and row["utm_campaign"] == "outreach",
          f"{r.status_code} {dict(row or {})}")
    r = post("nested-hp@example.com", group=9,
             body={"utm": {"campaign": "x" * 400}, "company": "bot"})
    with store.connect() as cx:
        row = cx.execute("SELECT utm_campaign FROM leads "
                         "WHERE email_key='nested-hp@example.com'").fetchone()
    check("honeypot devuelve 200 y NO persiste el lead (solo bloquea la IP)",
          r.status_code == 200 and row is None, f"{r.status_code} {dict(row or {})}")

    print("10. JSON malformado no rompe")
    r = c.post("/api/leads", data="no-json", content_type="application/json", headers=ip(8))
    check("400 invalid_email, no 500", r.status_code == 400, r.status_code)
    r = c.post("/api/leads", json=["array"], headers=ip(8))
    check("array JSON -> 400", r.status_code == 400, r.status_code)

    print("11. /api/stats protegido")
    check("404 sin STATS_TOKEN", c.get("/api/stats").status_code == 404)
    os.environ["STATS_TOKEN"] = "secreto"
    check("401 con token incorrecto",
          c.get("/api/stats", headers={"X-Stats-Token": "no"}).status_code == 401)
    r = c.get("/api/stats", headers={"X-Stats-Token": "secreto"})
    check("200 con token correcto", r.status_code == 200 and "leads" in r.get_json(), r.status_code)
    r = c.get("/api/stats", headers={"X-Stats-Token": "secreto"})
    check("queue_stats con UTMs", any(c["campana"] == "outreach" for c in r.get_json()["top_campanas"]),
          r.get_json()["top_campanas"])

    print("12. body grande -> 413")
    r = c.post("/api/leads", data={"email": "a@b.com", "pad": "z" * 20000}, headers=ip(9))
    check("413 payload_too_large",
          r.status_code == 413 and r.get_json()["error"] == "payload_too_large", r.status_code)

    print("13. la landing se sirve en el mismo origen")
    r = c.get("/")
    check("200 HTML", r.status_code == 200 and b"TaskFlow" in r.data, r.status_code)
    check("action=/api/leads presente", b'action="/api/leads"' in r.data)
    check("ruta inexistente -> index", c.get("/precios").status_code == 200)

    print()
    if FAILED:
        print(f"FALLARON {len(FAILED)}: " + ", ".join(FAILED))
        return 1
    print("Todas las pruebas pasaron.")
    return 0


if __name__ == "__main__":
    sys.exit(main())