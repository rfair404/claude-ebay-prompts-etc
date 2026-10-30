"""webapp/security.py — the localhost guard every route in the app runs (#311
slice 1). Moved out of webapp/freight.py, where it guarded /freight only, so
`server.py` can apply it app-wide.

Binding 127.0.0.1 stops other machines, not other web pages: a site the
operator visits can point its own hostname at 127.0.0.1 (DNS rebinding) and
then call the app as same-origin, or post a form at it cross-site (CSRF). So:

  * every request must name a loopback Host, or it gets a 403;
  * a write (anything but GET/HEAD/OPTIONS) must carry a loopback Origin.
    A write with no Origin is refused when it comes from a browser — a
    request carrying `Sec-Fetch-*` headers. Every current browser sends
    Origin on a POST, so a browser write without one is not something the
    app's own pages produce. A client with neither header (curl, urllib, an
    agent driving `/api/pager` during a show) is not a browser, so no web
    page can make it send anything, and it keeps working;
  * every answer gets the no-store/noindex headers, because several routes
    (/pick, /freight, the ship pages to come) carry buyer names and
    addresses.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response

PII_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Referrer-Policy": "no-referrer",
}

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _hostname(host_header: str) -> str:
    """`127.0.0.1:8770` -> `127.0.0.1`, `[::1]:8770` -> `::1`."""
    host = host_header.strip()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def is_loopback_origin(origin: str) -> bool:
    return (urlparse(origin).hostname or "") in LOCAL_HOSTS


def local_only(request: Request, response: Response) -> None:
    """FastAPI dependency: refuse a non-loopback Host, refuse a cross-site
    write, and stamp the no-store headers on the answer."""
    host = _hostname(request.headers.get("host") or "")
    if host not in LOCAL_HOSTS:
        raise HTTPException(403, f"served to localhost only, not {host!r}")
    if request.method not in SAFE_METHODS:
        origin = request.headers.get("origin")
        if origin:
            if not is_loopback_origin(origin):
                raise HTTPException(403, "cross-origin write refused")
        elif any(k.lower().startswith("sec-fetch-") for k in request.headers):
            raise HTTPException(403, "write refused: a browser sent no Origin")
    response.headers.update(PII_HEADERS)
