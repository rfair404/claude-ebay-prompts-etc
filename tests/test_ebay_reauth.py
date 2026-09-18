"""tools/ebay_reauth.py: code extraction and the config.yaml edit, off the network."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from ebay_reauth import extract_code, set_refresh_token   # noqa: E402

RAW = "v^1.1#i^1#p^3#r^1#I^3#f^0#t^Ul41XzY6QUJD"
ENC = "v%5E1.1%23i%5E1%23p%5E3%23r%5E1%23I%5E3%23f%5E0%23t%5EUl41XzY6QUJD"


def test_extract_code_from_full_redirect_url():
    url = f"https://auth2.ebay.com/oauth2/ThirdPartyAuthSucessFailure?isAuthSuccessful=true&code={ENC}&expires_in=299"
    assert extract_code(url) == RAW


def test_extract_code_from_bare_encoded_or_decoded_code():
    assert extract_code(ENC) == RAW
    assert extract_code(f'  "{RAW}"\n') == RAW


def test_extract_code_empty_when_url_has_no_code():
    assert extract_code("https://auth2.ebay.com/oauth2/ThirdPartyAuthSucessFailure?isAuthSuccessful=false") == ""


CFG = (
    "apify:\n  token: abc\n"
    "ebay:\n"
    "  # comment\n"
    '  environment: "production"\n'
    "\n"
    "  sandbox:\n"
    '    app_id: "s"\n'
    '    user_refresh_token: "SANDBOX-OLD"\n'
    "\n"
    "  production:\n"
    '    app_id: "p"\n'
    '    user_refresh_token: "PROD-OLD"\n'
    '    merchant_location_key: "ebaybiz-primary"\n'
    "store:\n  user_refresh_token: not-this-one\n"
)


def test_set_refresh_token_touches_only_the_active_environment_line():
    out = set_refresh_token(CFG, "production", "NEW")
    assert '    user_refresh_token: "NEW"\n' in out
    assert "SANDBOX-OLD" in out and "PROD-OLD" not in out
    assert "not-this-one" in out
    assert out.count("\n") == CFG.count("\n")


def test_set_refresh_token_keeps_crlf_line_endings():
    out = set_refresh_token(CFG.replace("\n", "\r\n"), "sandbox", "NEW")
    assert '    user_refresh_token: "NEW"\r\n' in out
    assert "PROD-OLD" in out


def test_set_refresh_token_raises_when_block_is_missing():
    with pytest.raises(ValueError):
        set_refresh_token(CFG, "staging", "NEW")
