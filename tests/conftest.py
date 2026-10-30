"""Suite-wide fixtures."""
import ipaddress
import os
import shutil
import socket
import sys
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def on_air_flag(tmp_path, monkeypatch):
    """Point the show-mode flag (show_flow.py writes it, bg_guard.py reads it)
    at a temp file, so a test that goes live with a fake OBS never marks the
    real show on air and blocks the next pytest run."""
    flag = tmp_path / "ON_AIR"
    monkeypatch.setenv("SHOW_ON_AIR_FLAG", str(flag))
    return flag


@pytest.fixture(autouse=True)
def empty_consignment_ledgers(tmp_path_factory, monkeypatch):
    """Point the consignment lookup (tools/consign.py, which lib/auto_promote
    asks before any ad) at an empty inventory/. No test reads Russell's real
    client ledgers, and a CI box or worktree without inventory/ doesn't make
    every promotion fail closed. A test that needs lots sets its own."""
    tools = str(Path(__file__).resolve().parent.parent / "tools")
    if tools not in sys.path:
        sys.path.append(tools)
    import consign
    monkeypatch.setattr(consign, "INVENTORY", tmp_path_factory.mktemp("inventory"))


# --- no network in tests (#311, "Rules for every slice") -------------------

class NetworkBlocked(ConnectionRefusedError):
    """A test tried to reach a host that isn't this machine. Fake it at
    `urlopen` (or stub `subprocess.run`) instead. Subclasses
    ConnectionRefusedError so code that already handles "offline" handles
    this the same way, rather than a test crashing somewhere new."""


def _is_loopback(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True                       # AF_UNIX path, or something not IP
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False                      # a hostname: never resolved, refused
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or bool(mapped and mapped.is_loopback)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Refuse any socket connect to a non-loopback address. Loopback stays
    open: tests/test_obsctl.py talks to a fake OBS on 127.0.0.1."""
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address):
            raise NetworkBlocked(f"tests may not reach the network: {address!r}")
        return real_connect(self, address)

    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _is_loopback(address):
            raise NetworkBlocked(f"tests may not reach the network: {address!r}")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture(autouse=True)
def hermetic_config(tmp_path_factory, monkeypatch):
    """No test sees the operator's credentials or config: every EBAY_* and
    APIFY_* variable is cleared, and EBAYBIZ_CONFIG points at a temp copy of
    tests/fixtures/config.yaml (a copy, so a test that writes config can't
    touch the tracked fixture). The loader's cache is dropped so the switch
    takes effect for a module that already read the real file."""
    for name in list(os.environ):
        if name.startswith(("EBAY_", "APIFY_")):
            monkeypatch.delenv(name, raising=False)
    cfg = tmp_path_factory.mktemp("config") / "config.yaml"
    shutil.copyfile(FIXTURES / "config.yaml", cfg)
    monkeypatch.setenv("EBAYBIZ_CONFIG", str(cfg))
    for mod in ("config", "lib.config"):
        loaded = sys.modules.get(mod)
        if loaded is not None and hasattr(loaded, "_cached_config"):
            monkeypatch.setattr(loaded, "_cached_config", None)
    return cfg
