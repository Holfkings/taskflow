"""Prueba end-to-end contra un servidor HTTP real (no el cliente de test).

Levanta gunicorn en un puerto libre, manda curl-equivalentes por HTTP de verdad
y verifica el flujo completo: JSON, form fallback, rate limit y assets.

    python backend/test_e2e.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FAILED = []


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def req(url, data=None, method=None, headers=None, follow=True):
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        if isinstance(data, dict):
            body = json.dumps(data).encode()
            hdrs.setdefault("Content-Type", "application/json")
        else:
            body = data.encode() if isinstance(data, str) else data
    r = urllib.request.Request(url, data=body, headers=hdrs, method=method or ("POST" if data else "GET"))

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None

    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(r, timeout=15) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "replace")


def check(name, cond, extra=None):  # noqa: ANN001
    extra = "" if extra is None else str(extra)
    print(("  ok  " if cond else "  FAIL") + f" {name}" + (f" -> {extra}" if extra and not cond else ""))
    if not cond:
        FAILED.append(name)


def start_server(port, env):
    """gunicorn en Linux/Render (produccion); werkzeug real en Windows (local).

    gunicorn importa fcntl, que no existe en Windows: instalarlo no lo hace
    ejecutable ahi, asi que la decision se toma por plataforma, no por import.
    """
    if os.name != "nt":
        return [sys.executable, "-m", "gunicorn", "--bind", f"127.0.0.1:{port}", "--workers", "1",
                "--chdir", HERE, "app:app"]
    return [sys.executable, os.path.join(HERE, "serve_dev.py"), str(port)]


def main():
    port = free_port()
    db = os.path.join(tempfile.mkdtemp(), "e2e.db")
    env = {**os.environ, "TASKFLOW_DB": db, "PORT": str(port), "IP_SALT": "e2e-salt",
           "LEAD_RATE_LIMIT": "3", "STATS_TOKEN": "tok-e2e"}
    cmd = start_server(port, env)
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"

    # HTTP real significa que todo sale de 127.0.0.1 y el limite es de 3/hora:
    # cada bloque de pruebas usa una IP propia (X-Forwarded-For) para no
    # arrastrar el contador del bloque anterior.
    def send(n, path="/api/leads", data=None, **kw):
        h = dict(kw.pop("headers", {}))
        h["X-Forwarded-For"] = f"10.0.{n}.1"
        return req(base + path, data, headers=h, **kw)
    try:
        ready = False
        for _ in range(40):  # esperar readiness
            try:
                if req(base + "/api/health")[0] == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.25)
        if not ready:
            print("El servidor no levanto. Log:")
            proc.terminate()
            print((proc.stdout.read() or "")[-2000:])
            return 1
        print(f"Servidor real arriba con: {' '.join(os.path.basename(c) for c in cmd)}\n")

        print("1. health por HTTP real")
        s, _, b = req(base + "/api/health")
        check("200 + ok true", s == 200 and json.loads(b)["ok"] is True, s)

        print("2. alta de lead (JSON)")
        s, _, b = send(2, data={"email": "E2E@Test.COM", "utm_source": "ads", "utm_campaign": "oct"})
        check("201", s == 201, s)
        check("duplicate false", json.loads(b)["duplicate"] is False, b)

        print("3. reenvio = 200 duplicate")
        s, _, b = send(2, data={"email": "e2e@test.com"})
        check("200 duplicate", s == 200 and json.loads(b)["duplicate"] is True, s)

        print("4. invalido = 400")
        s, _, b = send(4, data={"email": "no-es-email"})
        check("400 invalid_email", s == 400 and json.loads(b)["error"] == "invalid_email", s)

        print("5. honeypot: 200 falso y nada guardado")
        s, _, b = send(5, data={"email": "bot@x.com", "company": "llena"})
        check("200 ok true (el bot no sospecha)", s == 200 and json.loads(b)["ok"] is True, s)

        print("6. rate limit 3/hora por IP")
        codes = [send(6, data={"email": f"rl{i}@test.com"})[0] for i in range(4)]
        check("primeros 201, el 4to 429", codes[:3] == [201] * 3 and codes[3] == 429, codes)

        print("7. fallback sin JS: POST form sigue al #gracias (303)")
        s, h, _ = send(7, data="email=formbot%40test.com&utm_source=newsletter",
                       headers={"Content-Type": "application/x-www-form-urlencoded"}, follow=False)
        check("303", s == 303, s)
        check("Location termina en #gracias", h.get("Location", "").endswith("#gracias"), h.get("Location"))
        check("X-Lead-Status informational", h.get("X-Lead-Status") in {"201", "200"}, h.get("X-Lead-Status"))

        print("8. la landing y las rutas limpias responden 200")
        for i, path in enumerate(["/", "/index.html", "/precios", "/cualquier/cosa"]):
            s, _, b = send(80 + i, path=path)
            check(f"{path} -> 200 con HTML", s == 200 and "TaskFlow" in b, s)

        print("9. traversal bloqueado")
        s, _, b = send(90, path="/../../../../Windows/win.ini")
        check("no sirve archivos fuera del root", "TaskFlow" in b or s == 404, f"{s} {b[:80]}")

        print("10. stats protegido")
        check("401 sin token", req(base + "/api/stats")[0] == 401)
        s, _, b = req(base + "/api/stats", headers={"X-Stats-Token": "tok-e2e"})
        js = json.loads(b)
        check("200 con token", s == 200, s)
        check("conteo de leads > 0", js["leads"] > 0, js.get("leads"))
        check("UTMs en top_campanas", any(c["campana"] == "oct" for c in js["top_campanas"]),
              js["top_campanas"])

        print("11. body grande = 413")
        s, _, b = send(91, data={"email": "a@b.com", "pad": "z" * 40000})
        check("413", s == 413, s)

        print("12. metodo equivocado")
        s, _, _ = send(92, method="DELETE")
        check("405 en DELETE", s == 405, s)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if FAILED:
        print(f"FALLARON {len(FAILED)}: " + ", ".join(FAILED))
        return 1
    print("E2E real: todo en verde.")
    return 0


if __name__ == "__main__":
    sys.exit(main())