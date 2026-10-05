"""Servidor de desarrollo real para las pruebas E2E en Windows.

gunicorn no funciona en Windows (depende de fcntl), asi que en local levantamos
werkzeug en un hilo real con HTTP de verdad. En produccion corre gunicorn.

    python backend/serve_dev.py <puerto>
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from werkzeug.serving import make_server  # noqa: E402

import app as appmod  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    appmod.store.init_db()
    srv = make_server("127.0.0.1", port, appmod.app, threaded=True)
    print(f"serving on http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()