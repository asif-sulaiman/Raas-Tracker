"""WSGI entry point for production deployment with Waitress.

Usage:
    pip install waitress
    waitress-serve --host=0.0.0.0 --port=5000 --threads=4 wsgi:app

Environment variables:
    RAAS_SECRET  - Required in production. Secret key for the app.
    HOST             - Bind address (default: 127.0.0.1 for dev, 0.0.0.0 for prod).
    PORT             - Port number (default: 5000).
    FORCE_HTTPS      - Set to "1" to enable HSTS + HTTPS redirect.
    COOKIE_SECURE    - Set to "0" to disable Secure flag on cookies (dev only).
    PRODUCTION       - Set to "1" to require RAAS_SECRET.
    DISABLE_SETUP    - Set to "true" to disable the /setup endpoint.
    FLASK_ENV        - Set to "production" for production mode.
"""
from flask_app import app

if __name__ == "__main__":
    app.run()
