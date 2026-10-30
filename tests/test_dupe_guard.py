"""lib/dupe_guard.py — the same object photographed twice updates its row
instead of making a second one. Exercised through the two paths that create
rows: `tools/tg7_triage.py add` (camera triage and /intake catalog rows) and
`consign import` (client-ledger rows). Real CSVs and item folders in tmp_path;
the K01 rows are invented text in the shapes of the lot's real duplicates.

Only hard evidence attaches: the same item folder, or a frame filed in one
row's own item folder and on no other row. A shared bin walk-through frame,
the same bin and similar words only flag the new row (POSSIBLE_DUPE)."""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

SA = pytest.importorskip("sync_actuals", reason="sync_actuals imports ebay_client")
import consign  # noqa: E402
import dupe_guard  # noqa: E402
import stores  # noqa: E402
import tg7_triage  # noqa: E402

# Invented K01 rows, shaped like the real duplicates.
FLATWARE_LOT = dict(  # the C007 shape: a big mixed lot, seen in the bin frame
    item_id="K01-C007", bin="01", state="PREP",
    description="Silver-tone flatware shoebox lot: tied forks and knives, floral "
                "teaspoons, soup spoons, sugar shell, pierced server",
    photos="ZZ900214 ZZ900238")
GIFT_BAG = dict(  # the C008 shape: in the same bin frame, then shot on its own
    item_id="K01-C008", bin="03", state="PREP",
    description="White rope-handle gift bag of flatware: long serving pieces, "
                "large ladle spoon, forks, black-handled knife",
    photos="ZZ900238 ZZ900301 ZZ900302",
    inventory_folder="inventory/CONSIGN-K01/bin-03/C008-gift-bag-flatware")
OPENER = dict(  # the C022 shape: a bin frame, and a folder made for its shoot
    item_id="K01-C022", bin="07", state="INTAKE",
    description="Letter opener, steel blade, cream scrimshaw-style handle, ship scene",
    photos="ZZ900256", inventory_folder="inventory/CONSIGN-K01/K01-C022-letter-opener")
HARDWARE = dict(  # the C084 shape: the box in the same bin frame
    item_id="K01-C084", bin="07", state="INTAKE",
    description="Small hardware lot: hinges, screws, brass pulls", photos="ZZ900256")
AXE_A = dict(item_id="K01-C096", bin="05", state="INTAKE",
             description="Dug iron single-bit axe head, pitted, bagged with a note",
             photos="ZZ900835")
AXE_B = dict(item_id="K01-C100", bin="05", state="INTAKE",
             description="Iron single-bit axe head, no handle, dug, pitted",
             photos="ZZ900844")
BELT = dict(  # the C050 shape: one of several belts in a bin walk-through frame
    item_id="K01-C050", bin="03", state="INTAKE",
    description="Black leather belt, silver studs, roller buckle", photos="ZZ900500")


@pytest.fixture
def inv(tmp_path, monkeypatch):
    """An inventory/ under tmp_path with an empty K01 consignment folder."""
    monkeypatch.setattr(stores, "REPO", tmp_path)
    monkeypatch.setattr(stores, "load_config", lambda: {"storefronts": {"junk": {}}})
    for mod in (SA, consign):
        monkeypatch.setattr(mod, "REPO", tmp_path)
        monkeypatch.setattr(mod, "INVENTORY", tmp_path / "inventory")
    (tmp_path / "inventory" / "CONSIGN-K01").mkdir(parents=True)
    return tmp_path / "inventory"


def ledger(inv, *rows):
    """Write the K01 ledger, and file each row's frames that aren't the bin
    frame (the first one) in its item folder, as triage does."""
    path = inv / "CONSIGN-K01" / "client_ledger.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=consign.COLUMNS)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in consign.COLUMNS} for r in rows)
    for r in rows:
        if r.get("inventory_folder"):
            d = inv.parent / r["inventory_folder"]
            d.mkdir(parents=True, exist_ok=True)
            for f in r["photos"].split()[1:]:
                (d / f"{f}.JPG").write_bytes(b"jpg")
    return path


def read(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def by_id(path):
    return {r["item_id"]: r for r in read(path)}


def queue_of(folder):
    return [(r["action"], r["target"]) for r in read(folder / "dupe_queue.csv")]


def lot(inv, name="tg7-20991024-1526"):
    d = inv / name
    d.mkdir()
    return d


def add(catalog, description, photos, *extra):
    return tg7_triage.main(["add", str(catalog), "--description", description,
                            "--photos", photos, *extra])


def import_catalog(inv, rows, *extra):
    src = inv.parent / "incoming.csv"
    with src.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["no", "bin", "description", "qty", "photos",
                                           "why", "inventory_folder"])
        w.writeheader()
        w.writerows(rows)
    return consign.main(["import", "CONSIGN-K01", str(src), "--intake", "2099-10-22",
                         *extra])


# --------------------------------------------------------------------------- #
# consign import (client-ledger rows)
# --------------------------------------------------------------------------- #
def test_exact_resighting_on_import_attaches_to_the_row_on_record(inv, capsys):
    # C008 re-entered at intake carrying a frame from its own shoot.
    path = ledger(inv, FLATWARE_LOT, GIFT_BAG)
    again = dict(bin="03", qty="1 set", photos="ZZ900302 ZZ900310",
                 description="Flatware in a gift bag")
    assert import_catalog(inv, [again]) == 0
    assert "added 0 row(s)" in capsys.readouterr().out

    rows = by_id(path)
    assert sorted(rows) == ["K01-C007", "K01-C008"]          # no K01-C009
    assert rows["K01-C008"]["photos"] == "ZZ900238 ZZ900301 ZZ900302 ZZ900310"
    assert "re-run IDENTIFY" in rows["K01-C008"]["why"]
    assert rows["K01-C007"]["photos"] == FLATWARE_LOT["photos"]   # the lot is untouched
    assert queue_of(inv / "CONSIGN-K01") == [("ATTACHED", "K01-C008")]

    # the same catalog imported again changes nothing
    assert import_catalog(inv, [again]) == 0
    assert by_id(path) == rows
    assert len(read(inv / "CONSIGN-K01" / "dupe_queue.csv")) == 1


def test_an_attach_writes_the_client_ledger_through_consign(inv, monkeypatch):
    # One client-ledger write path (#380): the attach goes through
    # consign.write_ledger with the rows as read, so its cloud check applies.
    path = ledger(inv, FLATWARE_LOT, GIFT_BAG)
    calls = []
    real = consign.write_ledger
    monkeypatch.setattr(consign, "write_ledger",
                        lambda p, rows, base=None: calls.append((p, base)) or real(p, rows, base))
    assert import_catalog(inv, [dict(bin="03", qty="1 set", photos="ZZ900302 ZZ900310",
                                     description="Flatware in a gift bag")]) == 0
    attach_calls = [c for c in calls if c[1] is not None]
    assert [p for p, _ in attach_calls] == [path]
    assert [r["item_id"] for r in attach_calls[0][1]] == ["K01-C007", "K01-C008"]


def test_two_items_sharing_a_bin_frame_and_words_both_get_rows(inv, capsys):
    # Every item in a bin walk-through shares its frame and bin: no attach.
    path = ledger(inv, BELT)
    assert import_catalog(inv, [dict(bin="03", photos="ZZ900500",
                                     description="Brown leather belt, brass studs, "
                                                 "roller buckle")]) == 0
    assert "flagged 1" in capsys.readouterr().out
    rows = by_id(path)
    assert sorted(rows) == ["K01-C050", "K01-C051"]
    assert rows["K01-C050"]["photos"] == "ZZ900500"
    assert rows["K01-C050"]["why"] == ""
    (line,) = read(inv / "CONSIGN-K01" / "dupe_queue.csv")
    assert (line["action"], line["target"]) == ("POSSIBLE_DUPE", "K01-C051")
    assert line["matches"].startswith("K01-C050")


def test_genuinely_new_item_on_import_gets_its_own_row(inv):
    path = ledger(inv, FLATWARE_LOT, GIFT_BAG)
    assert import_catalog(inv, [dict(bin="09", description="Cast brass post office box "
                                     "door, eagle front, combination dial",
                                     photos="ZZ900693")]) == 0
    rows = by_id(path)
    assert sorted(rows) == ["K01-C007", "K01-C008", "K01-C009"]
    assert rows["K01-C009"]["state"] == "INTAKE"
    assert not (inv / "CONSIGN-K01" / "dupe_queue.csv").exists()


def test_ambiguous_lookalike_on_import_gets_a_row_and_a_queue_line(inv, capsys):
    path = ledger(inv, AXE_A, AXE_B)
    assert import_catalog(inv, [dict(bin="05", photos="ZZ900839",
                                     description="Iron single-bit axe head, dug")]) == 0
    assert "flagged 1" in capsys.readouterr().out
    assert sorted(by_id(path)) == ["K01-C096", "K01-C100", "K01-C101"]
    (line,) = read(inv / "CONSIGN-K01" / "dupe_queue.csv")
    assert (line["action"], line["target"]) == ("POSSIBLE_DUPE", "K01-C101")
    assert "K01-C096" in line["matches"] and "K01-C100" in line["matches"]


def test_new_flag_on_import_never_attaches_and_says_why(inv):
    path = ledger(inv, FLATWARE_LOT, GIFT_BAG)
    assert import_catalog(inv, [dict(bin="03", photos="ZZ900302",
                                     description="Second gift bag of flatware")],
                          "--new", "a second bag in the same frame") == 0
    rows = by_id(path)
    assert sorted(rows) == ["K01-C007", "K01-C008", "K01-C009"]
    assert rows["K01-C008"]["photos"] == GIFT_BAG["photos"]
    assert "new row forced (--new" in rows["K01-C009"]["why"]
    assert "a second bag in the same frame" in rows["K01-C009"]["why"]
    assert queue_of(inv / "CONSIGN-K01") == [("FORCED_NEW", "K01-C009")]


# --------------------------------------------------------------------------- #
# tg7_triage add (catalog rows from camera triage and /intake)
# --------------------------------------------------------------------------- #
def test_walk_through_then_individual_shoot_attaches_by_its_folder(inv, capsys):
    # C022: first seen in the bin frame (shared with C084), then shot on its
    # own into the folder its row names. The folder decides it; the bin frame
    # does not pull the shoot onto C084.
    path = ledger(inv, OPENER, HARDWARE)
    d = lot(inv)
    add(d / "catalog.csv", "Letter opener, scrimshaw-style handle, harbour scene",
        "ZZ900649 ZZ900650 ZZ900256", "--folder", OPENER["inventory_folder"])
    assert capsys.readouterr().out.startswith("ATTACH K01-C022")

    rows = by_id(path)
    assert rows["K01-C022"]["photos"] == "ZZ900256 ZZ900649 ZZ900650"
    assert "re-run IDENTIFY" in rows["K01-C022"]["why"]
    assert rows["K01-C084"]["photos"] == "ZZ900256"
    assert not (d / "catalog.csv").exists()                 # no parallel row
    assert queue_of(d) == [("ATTACHED", "K01-C022")]


def test_bin_frame_alone_never_attaches_a_catalog_row(inv, capsys):
    ledger(inv, OPENER, HARDWARE)
    d = lot(inv)
    add(d / "catalog.csv", "Small hardware lot: hinges, screws, brass pulls", "ZZ900256")
    out = capsys.readouterr().out
    assert out.startswith("NEW ") and "POSSIBLE_DUPE" in out
    (row,) = read(d / "catalog.csv")
    assert row["photos"] == "ZZ900256"
    assert queue_of(d) == [("POSSIBLE_DUPE", "inventory/tg7-20991024-1526/catalog.csv#01")]


def test_genuinely_new_object_gets_a_catalog_row_and_folder(inv, capsys):
    path = ledger(inv, OPENER, HARDWARE)
    d = lot(inv)
    add(d / "catalog.csv", "Henckels paring knife, carbon blade, wood grip, three rivets",
        "ZZ900657 ZZ900658", "--why", "possible value")
    out = capsys.readouterr().out
    assert "NEW inventory/tg7-20991024-1526/catalog.csv#01" in out
    assert "POSSIBLE_DUPE" not in out

    (row,) = read(d / "catalog.csv")
    assert (row["no"], row["bin"], row["photos"], row["why"]) == (
        "01", "tg7-20991024-1526", "ZZ900657 ZZ900658", "possible value")
    folder = inv.parent / row["inventory_folder"]
    assert folder.parent == d and folder.name.startswith("01-henckels-paring-knife")
    assert "description: Henckels paring knife" in (folder / "intake.txt").read_text()
    assert sorted(by_id(path)) == ["K01-C022", "K01-C084"]  # the ledger is untouched
    assert not (d / "dupe_queue.csv").exists()


def test_triage_hint_flags_the_new_row_against_the_row_named(inv, capsys):
    # The C022 / C109 case: triage saw a wood-handled knife and wrote "possible
    # duplicate of" the letter opener. The row is made and the hint flags it.
    ledger(inv, OPENER, HARDWARE)
    d = lot(inv)
    add(d / "catalog.csv", "Steel blade knife, wood handle, three rivets", "ZZ900657",
        "--hint", "possible duplicate of K01-C022")
    assert "POSSIBLE_DUPE" in capsys.readouterr().out
    (row,) = read(d / "catalog.csv")
    assert row["no"] == "01"
    (line,) = read(d / "dupe_queue.csv")
    assert line["action"] == "POSSIBLE_DUPE" and line["matches"].startswith("K01-C022")
    assert "triage hint" in line["matches"]


def test_new_flag_on_add_never_attaches_and_says_why(inv):
    path = ledger(inv, OPENER, HARDWARE)
    d = lot(inv)
    add(d / "catalog.csv", "Second letter opener, plain handle", "ZZ900660",
        "--folder", OPENER["inventory_folder"], "--new", "a second opener")
    (row,) = read(d / "catalog.csv")
    assert "new row forced (--new" in row["why"] and "a second opener" in row["why"]
    assert by_id(path)["K01-C022"]["photos"] == "ZZ900256"
    assert [a for a, _ in queue_of(d)] == ["FORCED_NEW"]


def test_attach_flag_puts_the_frames_on_the_row_named(inv):
    path = ledger(inv, AXE_A, AXE_B)
    d = lot(inv)
    add(d / "catalog.csv", "Iron single-bit axe head, dug", "ZZ900839",
        "--attach", "K01-C100")
    assert by_id(path)["K01-C100"]["photos"] == "ZZ900844 ZZ900839"
    assert by_id(path)["K01-C096"]["photos"] == "ZZ900835"
    assert not (d / "catalog.csv").exists()


def test_a_lot_mates_shared_folder_is_not_proof(inv):
    path = ledger(inv, dict(OPENER, lot_id="L1"), dict(HARDWARE, lot_id="L1",
                  inventory_folder=OPENER["inventory_folder"]))
    d = lot(inv)
    add(d / "catalog.csv", "Brass pull", "ZZ900670", "--folder", OPENER["inventory_folder"])
    assert len(read(d / "catalog.csv")) == 1
    assert by_id(path)["K01-C022"]["photos"] == "ZZ900256"


def test_evidence_rules():
    src = Path("inventory/CONSIGN-K01/client_ledger.csv")
    rows = [dupe_guard.Row(src, r) for r in (OPENER, HARDWARE)]
    same_words = dupe_guard.decide({"description": OPENER["description"]}, rows)
    assert same_words.action == "possible"                  # words never attach
    folder = dupe_guard.decide({"description": "x",
                                "inventory_folder": OPENER["inventory_folder"]}, rows)
    assert (folder.action, folder.best.row.key) == ("attach", "K01-C022")
    unrelated = dupe_guard.decide({"description": "Pyrex mixing bowl, yellow"}, rows)
    assert (unrelated.action, unrelated.matches) == ("new", [])
    assert dupe_guard.frames("ZZ240649.JPG ZZ240650 | retired (IN box)") == {
        "ZZ240649", "ZZ240650"}
