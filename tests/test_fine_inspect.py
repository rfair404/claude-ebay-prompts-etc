#!/usr/bin/env python3
"""lib/fine_inspect.py — pluggable high-resolution image inspector (GH #143).

Tested offline: all HTTP is faked by patching urllib.request.urlopen, same
pattern as tests/test_easypost_client.py. No network, no real API key
needed to run these.

Run:  python tests/test_fine_inspect.py
  or: pytest tests/test_fine_inspect.py
"""
import base64
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import fine_inspect as FI                                              # noqa: E402
from fine_inspect import (                                            # noqa: E402
    BACKENDS,
    DEFAULT_QUESTION,
    GeminiBackend,
    InspectionResult,
    VisionAPIError,
    VisionAuthError,
    fine_inspect,
    get_backend,
    render,
)
import config as CFG                                                  # noqa: E402
from config import ConfigError                                        # noqa: E402


# ---------------------------------------------------------------------------
# Fakes — same shape as tests/test_easypost_client.py's _FakeResponse/_Fake
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload, raw=None):
        self._raw = raw if raw is not None else json.dumps(payload).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, body=b'{"error":{"message":"boom"}}'):
    return urllib.error.HTTPError("https://x", code, f"HTTP {code}", None, io.BytesIO(body))


class _Fake:
    """Scripted stand-in for urllib.request.urlopen."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        step = self.script[min(len(self.requests), len(self.script)) - 1]
        if isinstance(step, Exception):
            raise step
        return step


def _patched(fake, fn, *, api_key="test-gemini-key"):
    """Run fn with urlopen faked and the API key forced via env var so no
    real config.yaml can leak in or out."""
    real_open = urllib.request.urlopen
    prev_env = os.environ.get("GEMINI_API_KEY")
    urllib.request.urlopen = fake
    if api_key is not None:
        os.environ["GEMINI_API_KEY"] = api_key
    else:
        os.environ.pop("GEMINI_API_KEY", None)
    try:
        return fn()
    finally:
        urllib.request.urlopen = real_open
        if prev_env is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = prev_env


def _gemini_response(text="STERLING 925"):
    return _FakeResponse({
        "candidates": [{"content": {"parts": [{"text": text}]}}],
    })


_TMPDIR = tempfile.TemporaryDirectory(prefix="fine_inspect_test_")
_tmp_counter = 0


def _image_path(tmp_bytes=b"\xff\xd8\xffnotarealjpegbutfine") -> Path:
    """A throwaway .jpg in a process-local temp dir — NOT tests/fixtures/,
    which is a force-tracked (see .gitignore) home for committed regression
    assets, not scratch files a test run generates and discards."""
    global _tmp_counter
    _tmp_counter += 1
    f = Path(_TMPDIR.name) / f"crop_{_tmp_counter}.jpg"
    f.write_bytes(tmp_bytes)
    return f


# ---------------------------------------------------------------------------
# config.py accessors
# ---------------------------------------------------------------------------

def test_get_gemini_api_key_missing_everywhere_raises_config_error():
    prev_env = os.environ.pop("GEMINI_API_KEY", None)
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {}
    try:
        try:
            CFG.get_gemini_api_key()
            raise AssertionError("expected ConfigError")
        except ConfigError as e:
            assert "GEMINI_API_KEY" in str(e)
            assert "vision" in str(e)
    finally:
        CFG.load_config = real_load
        if prev_env is not None:
            os.environ["GEMINI_API_KEY"] = prev_env


def test_get_gemini_api_key_prefers_env_over_config():
    prev_env = os.environ.get("GEMINI_API_KEY")
    os.environ["GEMINI_API_KEY"] = "from-env"
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {"vision": {"gemini": {"api_key": "from-config"}}}
    try:
        assert CFG.get_gemini_api_key() == "from-env"
    finally:
        CFG.load_config = real_load
        if prev_env is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = prev_env


def test_get_gemini_api_key_falls_back_to_config():
    prev_env = os.environ.pop("GEMINI_API_KEY", None)
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {"vision": {"gemini": {"api_key": "from-config"}}}
    try:
        assert CFG.get_gemini_api_key() == "from-config"
    finally:
        CFG.load_config = real_load
        if prev_env is not None:
            os.environ["GEMINI_API_KEY"] = prev_env


def test_get_gemini_model_defaults_when_unset():
    prev_env = os.environ.pop("GEMINI_VISION_MODEL", None)
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {}
    try:
        assert CFG.get_gemini_model() == CFG.DEFAULT_GEMINI_MODEL
    finally:
        CFG.load_config = real_load
        if prev_env is not None:
            os.environ["GEMINI_VISION_MODEL"] = prev_env


def test_get_gemini_model_env_overrides_config():
    prev_env = os.environ.get("GEMINI_VISION_MODEL")
    os.environ["GEMINI_VISION_MODEL"] = "gemini-env-model"
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {"vision": {"gemini": {"model": "gemini-config-model"}}}
    try:
        assert CFG.get_gemini_model() == "gemini-env-model"
    finally:
        CFG.load_config = real_load
        if prev_env is None:
            os.environ.pop("GEMINI_VISION_MODEL", None)
        else:
            os.environ["GEMINI_VISION_MODEL"] = prev_env


def test_get_vision_backend_defaults_to_gemini():
    prev_env = os.environ.pop("VISION_BACKEND", None)
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {}
    try:
        assert CFG.get_vision_backend() == "gemini"
    finally:
        CFG.load_config = real_load
        if prev_env is not None:
            os.environ["VISION_BACKEND"] = prev_env


def test_get_vision_backend_reads_config():
    prev_env = os.environ.pop("VISION_BACKEND", None)
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {"vision": {"backend": "some-other-engine"}}
    try:
        assert CFG.get_vision_backend() == "some-other-engine"
    finally:
        CFG.load_config = real_load
        if prev_env is not None:
            os.environ["VISION_BACKEND"] = prev_env


# ---------------------------------------------------------------------------
# Backend registry — the pluggability point
# ---------------------------------------------------------------------------

def test_get_backend_returns_gemini_by_default():
    prev_env = os.environ.get("GEMINI_API_KEY")
    os.environ["GEMINI_API_KEY"] = "k"
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {}
    try:
        backend = get_backend()
        assert isinstance(backend, GeminiBackend)
        assert backend.name == "gemini"
    finally:
        CFG.load_config = real_load
        if prev_env is None:
            os.environ.pop("GEMINI_API_KEY", None)
        else:
            os.environ["GEMINI_API_KEY"] = prev_env


def test_get_backend_unknown_name_raises_config_error_naming_available():
    try:
        get_backend("some-engine-that-does-not-exist")
        raise AssertionError("expected ConfigError")
    except ConfigError as e:
        assert "some-engine-that-does-not-exist" in str(e)
        assert "gemini" in str(e)


def test_backends_registry_only_contains_gemini_today():
    # Documents the pluggability contract: a new engine is added by
    # registering it here, nothing else changes.
    assert set(BACKENDS) == {"gemini"}
    assert BACKENDS["gemini"] is GeminiBackend


# ---------------------------------------------------------------------------
# GeminiBackend.inspect_images — request shape + response parsing
# ---------------------------------------------------------------------------

def test_inspect_images_sends_question_and_base64_image_inline():
    fake = _Fake(_gemini_response("STERLING 925"))
    img = _image_path()

    def go():
        backend = GeminiBackend(api_key="test-gemini-key", model="gemini-test-model")
        result = backend.inspect_images([img], "read the mark")
        assert result.text == "STERLING 925"
        assert result.backend == "gemini"
        assert result.model == "gemini-test-model"
        assert result.images == [str(img)]
        assert result.error is None

        req = fake.requests[0]
        assert "gemini-test-model:generateContent" in req.full_url
        assert "key=test-gemini-key" in req.full_url
        body = json.loads(req.data.decode())
        parts = body["contents"][0]["parts"]
        assert parts[0] == {"text": "read the mark"}
        inline = parts[1]["inline_data"]
        assert inline["mime_type"] == "image/jpeg"
        assert base64.b64decode(inline["data"]) == img.read_bytes()

    _patched(fake, go)


def test_inspect_images_accepts_multiple_images_in_one_call():
    fake = _Fake(_gemini_response("two crops read"))
    img1, img2 = _image_path(), _image_path(b"\x89PNG\r\n\x1a\nfakepngbytes")

    def go():
        backend = GeminiBackend(api_key="k")
        result = backend.inspect_images([img1, img2], "read both")
        assert result.images == [str(img1), str(img2)]
        body = json.loads(fake.requests[0].data.decode())
        # question + 2 inline_data parts
        assert len(body["contents"][0]["parts"]) == 3

    _patched(fake, go)


def test_inspect_images_401_raises_vision_auth_error():
    fake = _Fake(_http_error(401, b'{"error":{"message":"API key not valid"}}'))
    img = _image_path()

    def go():
        backend = GeminiBackend(api_key="bad-key")
        try:
            backend.inspect_images([img], "q")
            raise AssertionError("expected VisionAuthError")
        except VisionAuthError as e:
            assert "API key not valid" in str(e)

    _patched(fake, go)


def test_inspect_images_403_raises_vision_auth_error():
    fake = _Fake(_http_error(403, b'{"error":{"message":"permission denied"}}'))
    img = _image_path()

    def go():
        backend = GeminiBackend(api_key="k")
        try:
            backend.inspect_images([img], "q")
            raise AssertionError("expected VisionAuthError")
        except VisionAuthError:
            pass

    _patched(fake, go)


def test_inspect_images_500_raises_vision_api_error_with_status_and_body():
    fake = _Fake(_http_error(500, b'{"error":{"message":"internal error"}}'))
    img = _image_path()

    def go():
        backend = GeminiBackend(api_key="k")
        try:
            backend.inspect_images([img], "q")
            raise AssertionError("expected VisionAPIError")
        except VisionAPIError as e:
            assert e.status == 500
            assert "internal error" in e.body

    _patched(fake, go)


def test_inspect_images_network_error_raises_vision_api_error():
    fake = _Fake(urllib.error.URLError("reset"))
    img = _image_path()

    def go():
        backend = GeminiBackend(api_key="k")
        try:
            backend.inspect_images([img], "q")
            raise AssertionError("expected VisionAPIError")
        except VisionAPIError as e:
            assert e.status == 0

    _patched(fake, go)


def test_extract_text_returns_empty_string_for_no_candidates():
    # A safety-filter block or empty response must not raise or crash the
    # caller — it's a legitimate "nothing came back" the caller can render.
    assert FI._extract_text({"candidates": []}) == ""
    assert FI._extract_text({}) == ""


def test_extract_text_joins_multiple_parts():
    resp = {"candidates": [{"content": {"parts": [
        {"text": "line one"}, {"text": "line two"},
    ]}}]}
    assert FI._extract_text(resp) == "line one\nline two"


# ---------------------------------------------------------------------------
# fine_inspect() orchestrator — never raises
# ---------------------------------------------------------------------------

def test_fine_inspect_never_raises_on_missing_credentials():
    prev_env = os.environ.pop("GEMINI_API_KEY", None)
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {}
    img = _image_path()
    try:
        result = fine_inspect(img)
        assert isinstance(result, InspectionResult)
        assert result.ok is False
        assert "GEMINI_API_KEY" in result.error
    finally:
        CFG.load_config = real_load
        if prev_env is not None:
            os.environ["GEMINI_API_KEY"] = prev_env


def test_fine_inspect_never_raises_on_api_error():
    fake = _Fake(_http_error(500, b'{"error":{"message":"boom"}}'))
    img = _image_path()

    def go():
        result = fine_inspect(img)
        assert result.ok is False
        # VisionAPIError's message carries the status, not the body (mirrors
        # EasyPostAPIError) — the body is on the exception's .body attribute,
        # not surfaced through str(e)/result.error.
        assert "HTTP 500" in result.error
        assert result.backend == "gemini"

    _patched(fake, go)


def test_fine_inspect_accepts_a_single_path_or_a_list():
    fake = _Fake(_gemini_response("ok"))
    img = _image_path()

    def go():
        r1 = fine_inspect(img)
        assert r1.images == [str(img)]
        r2 = fine_inspect([img])
        assert r2.images == [str(img)]
        r3 = fine_inspect(str(img))
        assert r3.images == [str(img)]

    _patched(fake, go)


def test_fine_inspect_success_returns_populated_result():
    fake = _Fake(_gemini_response("EPNS 4213"))
    img = _image_path()

    def go():
        result = fine_inspect(img, question="read it")
        assert result.ok
        assert result.text == "EPNS 4213"
        assert result.model == CFG.DEFAULT_GEMINI_MODEL

    _patched(fake, go)


def test_fine_inspect_default_question_is_nonempty():
    assert isinstance(DEFAULT_QUESTION, str) and len(DEFAULT_QUESTION) > 20


def test_fine_inspect_unknown_backend_name_degrades_cleanly():
    img = _image_path()
    result = fine_inspect(img, backend_name="not-a-real-backend")
    assert result.ok is False
    assert "not-a-real-backend" in result.error


def test_fine_inspect_accepts_an_injected_backend_for_swap_in_testing():
    # Exercises the pluggability contract directly: any object satisfying
    # VisionBackend's interface works, without touching config at all.
    class _StubBackend(FI.VisionBackend):
        name = "stub"
        model = "stub-model"

        def inspect_images(self, image_paths, question):
            return InspectionResult(text="stubbed", backend=self.name,
                                    model=self.model,
                                    images=[str(p) for p in image_paths])

    img = _image_path()
    result = fine_inspect(img, backend=_StubBackend())
    assert result.ok
    assert result.text == "stubbed"
    assert result.backend == "stub"


# ---------------------------------------------------------------------------
# render()
# ---------------------------------------------------------------------------

def test_render_error_result():
    result = InspectionResult(text="", backend="gemini", model="?",
                              error="boom")
    out = render(result)
    assert "UNAVAILABLE" in out
    assert "boom" in out


def test_render_empty_text_result():
    result = InspectionResult(text="", backend="gemini", model="gemini-test",
                              images=["a.jpg"])
    out = render(result)
    assert "no legible text or marks" in out


def test_render_success_result():
    result = InspectionResult(text="STERLING\n925", backend="gemini",
                              model="gemini-test", images=["a.jpg", "b.jpg"])
    out = render(result)
    assert "gemini/gemini-test" in out
    assert "2 image(s)" in out
    assert "STERLING" in out
    assert "925" in out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_json_output_and_exit_code_on_success():
    fake = _Fake(_gemini_response("mark read"))
    img = _image_path()

    def go():
        real_argv = sys.argv
        sys.argv = ["fine_inspect.py", str(img), "--json"]
        buf = io.StringIO()
        from contextlib import redirect_stdout
        try:
            with redirect_stdout(buf):
                try:
                    FI._cli()
                except SystemExit as e:
                    assert e.code in (0, None)
        finally:
            sys.argv = real_argv
        out = json.loads(buf.getvalue())
        assert out["text"] == "mark read"
        assert out["error"] is None

    _patched(fake, go)


def test_cli_exits_nonzero_on_failure():
    prev_env = os.environ.pop("GEMINI_API_KEY", None)
    real_load = CFG.load_config
    CFG.load_config = lambda reload=False: {}
    img = _image_path()
    real_argv = sys.argv
    sys.argv = ["fine_inspect.py", str(img)]
    buf = io.StringIO()
    from contextlib import redirect_stdout
    try:
        with redirect_stdout(buf):
            try:
                FI._cli()
                raise AssertionError("expected SystemExit")
            except SystemExit as e:
                assert e.code == 1
    finally:
        sys.argv = real_argv
        CFG.load_config = real_load
        if prev_env is not None:
            os.environ["GEMINI_API_KEY"] = prev_env
    assert "UNAVAILABLE" in buf.getvalue()


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
