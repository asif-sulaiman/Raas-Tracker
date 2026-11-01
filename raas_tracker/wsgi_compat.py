"""WSGI compatibility for the hosts this app runs on.

Vercel's Python runtime hands the app a ``PATH_INFO`` that still contains
percent-escapes. PEP 3333 requires the server to decode it first, and the Flask
dev server, waitress and the test client all do, so the defect never shows
locally. Werkzeug then treats ``%20`` as three literal characters: a route
parameter captured from the path arrives as ``Production%20KNIT%20432`` and
every name lookup built on it misses.

``DecodePathInfo`` restores the WSGI contract outside routing, so route
parameters are the strings the caller sent. It is installed only where the
deployment declares the defect (``DECODE_PATH_INFO=1``): on a compliant server
a second pass would rewrite a name that literally contains an escape.
"""

from urllib.parse import unquote


class DecodePathInfo:
    """Percent-decode ``PATH_INFO`` once, before the app routes the request.

    Only escapes that are present are rewritten, and only the path is touched;
    the query string, other environ keys and the response are left alone.
    """
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        raw = environ.get("PATH_INFO") or ""
        if "%" in raw:
            decoded = unquote(raw)
            if decoded != raw:
                environ["PATH_INFO"] = decoded
        return self.app(environ, start_response)
