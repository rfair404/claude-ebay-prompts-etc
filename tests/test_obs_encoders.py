"""The show's encoder settings: eBay on the GPU, Twitch cheap enough for the CPU.

On the show laptop (4-core i7-8665U) two x264 encodes at 1080x1920 skipped
99% of frames ("Encoding overloaded", 2026-10-21). These pin the fix: the
Ebay profile template streams with VAAPI, and `twitch-setup` writes a
superfast, 720x1280 Custom Output using the keys OBS actually reads.
"""
from __future__ import annotations

import configparser
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "live"))

import show_flow as SF  # noqa: E402

PROFILE = ROOT / "tools" / "live" / "obs" / "profiles" / "Ebay"


def _ini() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cp.optionxform = str  # OBS keys are case-sensitive
    cp.read(PROFILE / "basic.ini")
    return cp


def test_ebay_profile_streams_on_the_gpu_at_full_size():
    cp = _ini()
    assert cp["AdvOut"]["Encoder"] == "ffmpeg_vaapi_tex"
    assert (cp["Video"]["OutputCX"], cp["Video"]["OutputCY"]) == ("1080", "1920")
    assert cp["AdvOut"]["UseRescale"] == "false"


def test_stream_encoder_settings_suit_whip_on_vaapi_and_x264():
    enc = json.loads((PROFILE / "streamEncoder.json").read_text())
    assert enc["rate_control"] == "CBR" and enc["bitrate"] == 3000 and enc["keyint_sec"] == 1
    assert enc["bf"] == 0                       # VAAPI: WHIP can't carry B-frames
    assert "bframes=0" in enc["x264opts"]       # the same for the x264 fallback
    assert enc["preset"] == "superfast"


def test_twitch_output_is_superfast_and_rescaled_with_keys_obs_reads():
    out = SF.TWITCH_OUT
    assert out["FFVEncoder"] == "libx264"       # Custom Output has no VAAPI path
    assert out["FFVCustom"] == "preset=superfast"
    # frontend/utility/AdvancedOutput.cpp SetupFFmpeg reads FFRescale/FFRescaleRes
    assert out["FFRescale"] == "true" and out["FFRescaleRes"] == "720x1280"
    assert "FFUseRescale" not in out
    assert out["FFVBitrate"] == "3000" and out["FFVGOPSize"] == "60"  # 2 s keyframes at 30 fps
