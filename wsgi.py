"""WSGI entry point - imports app from flask_app (which already has ProxyFix)."""

import os
from flask_app import app

if __name__ == "__main__":
    # Allow running wsgi.py directly for testing
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG") == "1"
    app.run(debug=debug, host=host, port=port)