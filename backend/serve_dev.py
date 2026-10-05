"""Servidor de desarrollo real para las pruebas E2E en Windows.

gunicorn no funciona en Windows (depende de fcntl), asi que en local levantamos
werkzeug en un hilo real con HTTP de verdad. En produccion corre gunicorn.

    python backend/serve_dev.py <puerto>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# El default de store.DB_PATH es "leads.db" RELATIVO al CWD: correr el server
# desde la raiz del repo dejaba la base de leads (con emails reales) tirada en
# C:/Users/muell/taskflow/leads.db. Gitignored, pero es donde nadie la busca.
if not os.environ.get("TASKFLOW_DB"):
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev-leads.db")
    os.environ["TASKFLOW_DB"] = db
    print(f"[dev] TASKFLOW_DB no estaba: usando {db} (NO es la de produccion)")

from werkzeug.serving import make_server  # noqa: E402

import app as appmod  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    appmod.store.init_db()
    srv = make_server("127.0.0.1", port, appmod.app, threaded=True)
    print(f"serving on http://127.0.0.1:{port} (db={appmod.store.DB_PATH})", flush=True)
    srv.serve_forever()