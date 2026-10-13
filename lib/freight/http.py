"""One JSON-over-HTTPS helper for every freight provider.

Retry policy is easypost_client.api_send's, for the same reason: a 5xx is
retried only on idempotent methods, and a network error is retried only when
the caller says the call is safe to repeat. A booking is never safe to repeat
— a lost response may be a pickup already dispatched and a card already
charged — so book paths pass retry_network_errors=False.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


class FreightAPIError(RuntimeError):
    def __init__(self, provider: str, status: int, message: str,
                 body: Optional[str] = None) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status = status
        self.body = body


class FreightAuthError(FreightAPIError):
    """401/403 — the key is missing, wrong, or lacks the plan for this call."""


def send(provider: str, method: str, url: str, *, body: Optional[dict] = None,
         headers: Optional[dict] = None, params: Optional[dict] = None,
         timeout: float = 60, retry_network_errors: bool = True) -> object:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None})
    data = json.dumps(body).encode("utf-8") if body is not None else None
    h = {"Accept": "application/json", "User-Agent": "ebaybiz-freight/1"}
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})

    m = method.upper()
    idempotent = m in ("GET", "PUT", "DELETE")
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method=m, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            path = urllib.parse.urlparse(url).path
            if e.code in (401, 403):
                raise FreightAuthError(provider, e.code,
                                       f"{m} {path} -> HTTP {e.code}: {text[:300]}",
                                       text) from e
            if e.code >= 500 and idempotent and attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise FreightAPIError(provider, e.code,
                                  f"{m} {path} -> HTTP {e.code}: {text[:300]}",
                                  text) from e
        except urllib.error.URLError as e:
            if retry_network_errors and attempt < 2:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise FreightAPIError(provider, 0, f"{m} {url} -> network error: {e}") from e
    raise AssertionError("unreachable")
