#!/usr/bin/env python3
"""SHOOT — an interactive photo shoot, one item at a time, with an agent at the controls.

Russell puts an item on the podium and says what it is. The agent captures,
looks at a small copy of each frame, and logs what it sees. "Done for now"
closes the item and the next one starts. The cameras and pages are
show_flow's (D5100 stills pause live view behind Up Next, like a refocus;
TG-7 stills don't pause anything); this adds the shoot's folders and log on
top. Nothing here streams, so it runs the same on air or off. On air,
`show_flow.py still` (and `step` from the item view) is `capture tg7` that
also puts the photo on air: with an item open its frame lands here, logged.

  inventory/<shoot>/              the shoot; a bucket, so its context.txt
    shoot.json                    (lib/dir_context.py) reaches every item
    01-brass-lamp/                one item: its full-size frames, as shot
      D5_20261021_214112.jpg
      TG7_20261021_214230.jpg
      shoot_log.md                what Russell said and what the agent saw
      .shoot/                     small copies for looking: <frame>.jpg, look.jpg

    python tools/live/shoot.py start [NAME]               # new or resume; default shoot-YYYYMMDD
    python tools/live/shoot.py item [SLUG] [--note TEXT]  # the next item (closes the open one)
    python tools/live/shoot.py name SLUG                  # rename the open item once it's known
    python tools/live/shoot.py capture d5|tg7 [--note TEXT]
    python tools/live/shoot.py look                       # what's on air now -> .shoot/look.jpg
    python tools/live/shoot.py log TEXT [--by russell]    # default --by claude
    python tools/live/shoot.py done                       # close the item: frames vs the house target
    python tools/live/shoot.py status

The full-size frames are what IDENTIFY, PREP and INVESTIGATE read later. The
agent looks only at the copies in .shoot/ (prompts/_shared.md: never a raw
frame in the main thread).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import obsctl  # noqa: E402
import show_flow  # noqa: E402

INVENTORY = show_flow.REPO / "inventory"
CURRENT = INVENTORY / ".current_shoot"  # one line: the shoot being worked
LOG = "shoot_log.md"
LOOK_DIR = ".shoot"
THUMB = 800  # long edge of the copies the agent reads: marks and defects still legible
TARGET = 10  # the house frame target (prompts/prep.md)
FRAME_EXT = (".jpg", ".jpeg")  # what counts as a frame; a RAW beside its JPEG is the same shot


class ShootError(RuntimeError):
    pass


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not s:
        raise ShootError(f"{text!r} has no letters or digits to name a folder with")
    return s[:48]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --- the shoot's state ---------------------------------------------------------

def shoot_dir(name: str | None = None) -> Path:
    if name is None:
        try:
            name = CURRENT.read_text().strip()
        except OSError:
            name = ""
        if not name:
            raise ShootError("no shoot started; run `shoot.py start [NAME]`")
    return INVENTORY / slug(name)


def load(d: Path) -> dict:
    try:
        return json.loads((d / "shoot.json").read_text())
    except FileNotFoundError:
        raise ShootError(f"{d} has no shoot.json; run `shoot.py start {d.name}`") from None


def save(d: Path, st: dict) -> None:
    tmp = d / "shoot.json.tmp"
    tmp.write_text(json.dumps(st, indent=2) + "\n")
    tmp.replace(d / "shoot.json")


def open_shoot() -> tuple[Path, dict] | None:
    """(shoot dir, its state) when a shoot is started and has an item open,
    else None. show_flow.py's `still` saves its TG-7 frame there if so."""
    try:
        d = shoot_dir()
        st = load(d)
    except ShootError:
        return None
    return (d, st) if st.get("current") else None


def open_item(d: Path, st: dict) -> Path:
    if not st.get("current"):
        raise ShootError("no item open; run `shoot.py item [SLUG]` when it's on the podium")
    return d / st["current"]


def frames(item: Path) -> list[Path]:
    return sorted(p for p in item.iterdir() if p.is_file() and p.suffix.lower() in FRAME_EXT)


def log(item: Path, line: str) -> None:
    with open(item / LOG, "a", encoding="utf-8") as f:
        f.write(f"- {datetime.now():%H:%M} {line}\n")


def thumb(frame: Path) -> Path:
    """A small, upright copy of `frame` in .shoot/ for the agent to read."""
    from PIL import Image, ImageOps  # only the commands that make copies need Pillow
    out = frame.parent / LOOK_DIR / f"{frame.stem}.jpg"
    out.parent.mkdir(exist_ok=True)
    with Image.open(frame) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((THUMB, THUMB))
        im.convert("RGB").save(out, quality=85)
    return out


# --- commands --------------------------------------------------------------------

def start(name: str | None) -> dict:
    name = slug(name) if name else f"shoot-{datetime.now():%Y%m%d}"
    d = INVENTORY / name
    d.mkdir(parents=True, exist_ok=True)
    resumed = (d / "shoot.json").exists()
    if not resumed:
        save(d, {"shoot": name, "started": now(), "items": [], "current": None})
    CURRENT.write_text(name + "\n")
    return {"shoot": name, "dir": str(d), "resumed": resumed, **summary(d, load(d))}


def done(d: Path, st: dict) -> dict | None:
    """Close the open item (if any): stamp it and log its frame count."""
    if not st.get("current"):
        return None
    item = d / st["current"]
    n = len(frames(item))
    short = f" (house target {TARGET})" if n < TARGET else ""
    log(item, f"done: {n} frames{short}")
    for it in st["items"]:
        if it["dir"] == st["current"]:
            it["done"] = now()
    st["current"] = None
    save(d, st)
    return {"item": item.name, "frames": n, "target": TARGET,
            "names": [f.name for f in frames(item)]}


def new_item(d: Path, st: dict, name: str | None, note: str | None) -> dict:
    closed = done(d, st)
    n = len(st["items"]) + 1
    folder = f"{n:02d}-{slug(name)}" if name else f"{n:02d}"
    item = d / folder
    item.mkdir()
    (item / LOG).write_text(f"# Shoot log\n\nShoot: {st['shoot']} · item {n} · "
                            f"started {datetime.now():%Y-%m-%d %H:%M}\n\n", encoding="utf-8")
    if note:
        log(item, f"**Russell:** {note}")
    st["items"].append({"dir": folder, "started": now(), "done": None})
    st["current"] = folder
    save(d, st)
    return {"item": folder, "dir": str(item), "closed": closed}


def rename(d: Path, st: dict, name: str) -> dict:
    item = open_item(d, st)
    num = item.name.split("-", 1)[0]
    to = d / f"{num}-{slug(name)}"
    if to != item:
        if to.exists():
            raise ShootError(f"{to.name} already exists")
        item.rename(to)
        for it in st["items"]:
            if it["dir"] == item.name:
                it["dir"] = to.name
        st["current"] = to.name
        save(d, st)
        log(to, f"renamed from {item.name}")
    return {"item": to.name, "dir": str(to)}


def capture(d: Path, st: dict, camera: str, note: str | None) -> dict:
    item = open_item(d, st)
    focus = None
    if camera == "d5":
        c = show_flow.connect()
        try:
            got = [Path(f) for f in show_flow.still_d5(c, item)["files"]]
        finally:
            c.close()
    else:
        tg7 = show_flow.tg7cam()
        try:
            path, af = tg7.shoot(to=item)
        except tg7.CamError as e:
            raise ShootError(f"TG-7: {e}") from e
        got, focus = [path], af != "ng"
    looks = [thumb(f) for f in got if f.suffix.lower() in FRAME_EXT]
    flag = ", focus didn't lock" if focus is False else ""
    log(item, f"capture **{' + '.join(f.name for f in got)}** ({camera}{flag})"
              + (f" — {note}" if note else ""))
    return {"item": item.name, "camera": camera, "files": [str(f) for f in got],
            "look": [str(p) for p in looks], "focused": focus, "frames": len(frames(item)),
            "target": TARGET}


def look(d: Path, st: dict) -> dict:
    """OBS's program picture as it is now, for the agent to see the podium."""
    where = (d / st["current"]) if st.get("current") else d
    out = where / LOOK_DIR / "look.jpg"
    out.parent.mkdir(exist_ok=True)
    c = show_flow.connect()
    try:
        scene = show_flow.program(c)
        out.write_bytes(show_flow.program_shot(c, width=540, quality=80))
    finally:
        c.close()
    return {"look": str(out), "on_air": show_flow.page_of(scene) or scene}


def summary(d: Path, st: dict) -> dict:
    out = {"items": len(st["items"]), "current": st.get("current")}
    if st.get("current"):
        item = d / st["current"]
        out["frames"] = len(frames(item))
        lines = (item / LOG).read_text(encoding="utf-8").splitlines()
        out["recent"] = [ln for ln in lines if ln.startswith("- ")][-5:]
    return out


# --- CLI ---------------------------------------------------------------------------

def report_done(r: dict | None) -> None:
    if r:
        short = "" if r["frames"] >= r["target"] else f"  (house target {r['target']})"
        print(f"done: {r['item']}: {r['frames']} frames{short}")
        if r["names"]:
            print("  " + ", ".join(r["names"]))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--shoot", help="the shoot's folder name under inventory/ (default: the current one)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="start a shoot, or resume one")
    s.add_argument("name", nargs="?")
    s = sub.add_parser("item", help="the next item on the podium (closes the open one)")
    s.add_argument("name", nargs="?", help="a short slug, if it's known yet")
    s.add_argument("--note", help="what Russell said about it")
    s = sub.add_parser("name", help="rename the open item")
    s.add_argument("name")
    s = sub.add_parser("capture", help="take a full-size still into the open item")
    s.add_argument("camera", choices=["d5", "tg7"])
    s.add_argument("--note", help="what the frame is for (hero, underside, the chip on the rim)")
    sub.add_parser("look", help="OBS's program picture now -> .shoot/look.jpg")
    s = sub.add_parser("log", help="add a line to the open item's shoot log")
    s.add_argument("text")
    s.add_argument("--by", choices=["claude", "russell"], default="claude")
    sub.add_parser("done", help="close the open item")
    sub.add_parser("status")
    a = p.parse_args(argv)
    try:
        if a.cmd == "start":
            r = start(a.shoot or a.name)
            print(f"{'resumed' if r['resumed'] else 'started'}: {r['shoot']}  ({r['dir']})")
            print(f"items: {r['items']}  open: {r['current'] or '-'}")
            return 0
        d = shoot_dir(a.shoot)
        st = load(d)
        if a.cmd == "item":
            r = new_item(d, st, a.name, a.note)
            report_done(r["closed"])
            print(f"open: {r['item']}  ({r['dir']})")
        elif a.cmd == "name":
            r = rename(d, st, a.name)
            print(f"open: {r['item']}  ({r['dir']})")
        elif a.cmd == "capture":
            r = capture(d, st, a.camera, a.note)
            print(f"{r['camera']}: " + ", ".join(Path(f).name for f in r["files"])
                  + ("  FOCUS DIDN'T LOCK" if r["focused"] is False else ""))
            for lk in r["look"]:
                print(f"look: {lk}")
            print(f"{r['item']}: {r['frames']}/{r['target']} frames")
        elif a.cmd == "look":
            r = look(d, st)
            print(f"look: {r['look']}  (on air: {r['on_air']})")
        elif a.cmd == "log":
            log(open_item(d, st), f"**{a.by.capitalize()}:** {a.text}")
        elif a.cmd == "done":
            r = done(d, st)
            if r is None:
                print("no item open")
            report_done(r)
        else:
            r = summary(d, st)
            print(f"shoot: {d.name}  items: {r['items']}  open: {r['current'] or '-'}")
            if r["current"]:
                print(f"frames: {r['frames']}/{TARGET}")
                for ln in r["recent"]:
                    print(f"  {ln}")
    except (ShootError, obsctl.ObsError, OSError) as e:  # OSError: OBS's socket or the TG-7's Wi-Fi dropped
        print(f"shoot: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
