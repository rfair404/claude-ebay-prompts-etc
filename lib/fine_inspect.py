"""
fine_inspect — pluggable high-resolution image inspector for IDENTIFY/PREP.

Claude's own read of a photo can miss or misread the things that live at
the pixel level: fine print, a raised/embossed maker's mark with no
printed contrast to key off, a faint signature. This module hands those
crops to a dedicated vision model instead of guessing. The backend is
pluggable — today it's Gemini (`GeminiBackend`); swapping in a different
high-resolution vision engine later means adding one class + registering
it in BACKENDS below, not touching any caller (see prompts/identify.md).

Distinct from lib/lens_id.py: Lens does REVERSE-IMAGE SEARCH (what does
this look like on the web — a design/maker lookup, hosted publicly,
tallied across matches). This module does DIRECT VISUAL INSPECTION (the
image bytes go straight to a vision model with one targeted question, one
answer) — for reading what's actually ON the photo, not what it resembles.
No public hosting involved.

Key source (same precedence as every other API key in this repo, see
config.py):
    1. GEMINI_API_KEY environment variable
    2. config.yaml `vision.gemini.api_key`
    3. ConfigError, with setup instructions

CLI:
    python lib/fine_inspect.py <crop.jpg>
    python lib/fine_inspect.py <crop.jpg> --question "Read the mark stamped into the base."
    python lib/fine_inspect.py <crop.jpg> <crop2.jpg> --json
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import ConfigError, get_gemini_api_key, get_gemini_model, get_vision_backend  # noqa: E402

DEFAULT_QUESTION = (
    "Zoom into this image and read every mark, stamp, signature, or fine "
    "print visible on it as precisely as possible. Transcribe any legible "
    "text VERBATIM, including partially-worn characters (mark uncertain "
    "ones with brackets, e.g. \"S[T?]ERLING\"). For a raised/embossed mark "
    "with no printed contrast, describe its shape, symbols, and placement "
    "even if you can't read letters off it. If nothing is legible, say so "
    "plainly rather than guessing a maker."
)

_MIME_BY_SUFFIX = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
}


def _mime_type(path: Path) -> str:
    return _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/jpeg")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class VisionAuthError(RuntimeError):
    """Raised when the vision backend rejects the credentials (HTTP 401/403)."""


class VisionAPIError(RuntimeError):
    """Raised when a vision backend call returns a non-2xx / error response."""

    def __init__(self, status: int, message: str, body: Optional[str] = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


# ---------------------------------------------------------------------------
# Result shape — identical for every backend, so callers never branch on
# which engine answered.
# ---------------------------------------------------------------------------

@dataclass
class InspectionResult:
    text: str
    backend: str
    model: str
    images: list[str] = field(default_factory=list)
    raw: Optional[dict] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Pluggable backend interface
# ---------------------------------------------------------------------------

class VisionBackend(ABC):
    """One high-resolution image inspection engine. Add a new one by
    subclassing this and registering it in BACKENDS below — no caller of
    fine_inspect()/get_backend() needs to change."""

    name: str

    @abstractmethod
    def inspect_images(self, image_paths: list[Path], question: str) -> InspectionResult:
        ...


class GeminiBackend(VisionBackend):
    """Google Gemini `generateContent`, called directly with inline image
    bytes — unlike lib/lens_id.py's reverse-image search, the image never
    leaves this process except in the API call itself."""

    name = "gemini"
    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self.api_key = api_key or get_gemini_api_key()
        self.model = model or get_gemini_model()

    def inspect_images(self, image_paths: list[Path], question: str) -> InspectionResult:
        parts: list[dict] = [{"text": question}]
        for p in image_paths:
            data = base64.b64encode(Path(p).read_bytes()).decode("ascii")
            parts.append({"inline_data": {"mime_type": _mime_type(Path(p)), "data": data}})
        resp = self._send({"contents": [{"parts": parts}]})
        return InspectionResult(
            text=_extract_text(resp), backend=self.name, model=self.model,
            images=[str(p) for p in image_paths], raw=resp)

    def _send(self, body: dict) -> dict:
        url = f"{self.API_BASE}/{self.model}:generateContent?key={self.api_key}"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            if e.code in (401, 403):
                raise VisionAuthError(
                    f"Gemini rejected the API key (HTTP {e.code}): {body_text}") from e
            raise VisionAPIError(e.code, f"generateContent -> HTTP {e.code}",
                                 body_text) from e
        except urllib.error.URLError as e:
            raise VisionAPIError(0, f"generateContent -> network error: {e}", None) from e


def _extract_text(resp: dict) -> str:
    """Pull concatenated text out of Gemini's `candidates[].content.parts[]`
    shape. A blocked/empty response (safety filter, no candidates) yields
    "" rather than raising — the caller decides whether that's an error."""
    candidates = resp.get("candidates") or []
    if not candidates:
        return ""
    content = candidates[0].get("content") or {}
    texts = [p.get("text", "") for p in content.get("parts") or [] if p.get("text")]
    return "\n".join(texts).strip()


# ---------------------------------------------------------------------------
# Backend registry — the pluggability point
# ---------------------------------------------------------------------------

BACKENDS: dict[str, type] = {
    "gemini": GeminiBackend,
}


def get_backend(name: Optional[str] = None) -> VisionBackend:
    """Instantiate the configured (or named) vision backend.

    Precedence for `name`: explicit arg > config.yaml `vision.backend` >
    "gemini". Raises ConfigError for an unknown backend name, and whatever
    the backend's own constructor raises for missing credentials.
    """
    resolved = (name or get_vision_backend()).lower()
    cls = BACKENDS.get(resolved)
    if cls is None:
        raise ConfigError(
            f"Unknown vision backend {resolved!r}. Available: "
            f"{', '.join(sorted(BACKENDS))}. Set vision.backend in "
            f"config.yaml, or register a new backend in "
            f"lib/fine_inspect.py's BACKENDS dict.")
    return cls()


# ---------------------------------------------------------------------------
# Orchestrate — never raises; caller degrades to its own read on error
# ---------------------------------------------------------------------------

def fine_inspect(images, question: str = DEFAULT_QUESTION, *,
                 backend: Optional[VisionBackend] = None,
                 backend_name: Optional[str] = None) -> InspectionResult:
    """Run one high-resolution inspection pass over one or more photos.

    Mirrors lib/lens_id.lens_opinion()'s contract: never raises. On any
    failure (missing credentials, unknown backend, network, API error) this
    returns an InspectionResult with `error` set so IDENTIFY/PREP can
    degrade to their own read / needs_followup_photo instead of crashing
    the shoot.
    """
    paths = [Path(images)] if isinstance(images, (str, Path)) else [Path(p) for p in images]
    if backend is None:
        try:
            backend = get_backend(backend_name)
        except Exception as e:  # noqa: BLE001 - report, never raise
            return InspectionResult(text="", backend=backend_name or "?", model="?",
                                    images=[str(p) for p in paths], error=str(e))
    try:
        return backend.inspect_images(paths, question)
    except Exception as e:  # noqa: BLE001 - report, never raise
        return InspectionResult(text="", backend=backend.name,
                                model=getattr(backend, "model", "?"),
                                images=[str(p) for p in paths], error=str(e))


def render(result: InspectionResult) -> str:
    """Human-readable block IDENTIFY/PREP folds into their reasoning."""
    if not result.ok:
        return f"Fine inspection ({result.backend}): UNAVAILABLE — {result.error}"
    header = (f"Fine inspection ({result.backend}/{result.model}, "
             f"{len(result.images)} image(s)):")
    if not result.text:
        return f"{header}\n  (no legible text or marks reported)"
    body = "\n".join(f"  {line}" for line in result.text.splitlines())
    return f"{header}\n{body}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass
    ap = argparse.ArgumentParser(
        description="Pluggable high-resolution image inspector (fine print, "
                    "raised marks, signatures) for IDENTIFY/PREP")
    ap.add_argument("image", nargs="+", help="One or more photo crops to inspect")
    ap.add_argument("--question", default=DEFAULT_QUESTION,
                    help="What to ask the vision model")
    ap.add_argument("--backend", help="Override the configured backend (default: gemini)")
    ap.add_argument("--json", action="store_true", help="Emit the full result as JSON")
    args = ap.parse_args()

    result = fine_inspect(args.image, question=args.question, backend_name=args.backend)
    if args.json:
        print(json.dumps({
            "text": result.text, "backend": result.backend, "model": result.model,
            "images": result.images, "error": result.error,
        }, indent=2, ensure_ascii=False))
    else:
        print(render(result))
    if not result.ok:
        sys.exit(1)


if __name__ == "__main__":
    _cli()
