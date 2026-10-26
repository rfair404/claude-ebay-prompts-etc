"""lib/dupe_guard.py — the same object photographed twice updates its row
instead of making a second one. Exercised through the two paths that create
rows: `tools/tg7_triage.py add` (camera triage and /intake catalog rows) and
`consign import` (client-ledger rows). Real CSVs in tmp_path; the K01 rows are
invented text in the shapes of the lot's real duplicates."""
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
FLATWARE_LOT = dict(  # the C007 shape: a big mixed lot
    item_id="K01-C007", bin="01", state="PREP",
    description="Silver-tone flatware shoebox lot: tied forks and knives, floral "
                "teaspoons, soup spoons, sugar shell, pierced server",
    photos="ZZ900214 ZZ900232",
    inventory_folder="inventory/CONSIGN-K01/bin-01/C007-flatware-shoebox-lot")
GIFT_BAG = dict(  # the C008 shape: a set first seen in a walk-through frame
    item_id="K01-C008", bin="03", state="PREP",
    description="White rope-handle gift bag of flatware: long serving pieces, "
                "large ladle spoon, forks, black-handled knife",
    photos="ZZ900238")
OPENER = dict(  # the C022 shape: a bin frame only, no folder yet
    item_id="K01-C022", bin="07", state="INTAKE",
    description="Letter opener, steel blade, cream scrimshaw-style handle, ship scene",
    photos="ZZ900256")
HARDWARE = dict(  # the C084 shape: the box the knife came out of
    item_id="K01-C084", bin="07", state="INTAKE",
    description="Small hardware lot: hinges, screws, brass pulls",
    photos="ZZ900256")
AXE_A = dict(item_id="K01-C096", bin="05", state="INTAKE",
             description="Dug iron single-bit axe head, pitted, bagged with a note",
             photos="ZZ900835")
AXE_B = dict(item_id="K01-C100", bin="05", state="INTAKE",
             description="Iron single-bit axe head, no handle, dug, pitted",
             photos="ZZ900844")


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
    path = inv / "CONSIGN-K01" / "client_ledger.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=consign.COLUMNS)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in consign.COLUMNS} for r in rows)
    return path


def read(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def by_id(path):
    return {r["item_id"]: r for r in read(path)}


def lot(inv, name="tg7-20991024-1526"):
    d = inv / name
    d.mkdir()
    return d


def add(catalog, description, photos, *extra):
    return tg7_triage.main(["add", str(catalog), "--description", description,
                            "--photos", photos, *extra])


def import_catalog(inv, tmp_path_rows, *extra):
    src = inv.parent / "incoming.csv"
    with src.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["no", "bin", "description", "qty", "photos",
                                           "why"])
        w.writeheader()
        w.writerows(tmp_path_rows)
    return consign.main(["import", "CONSIGN-K01", str(src), "--intake", "2099-10-22",
                         *extra])


# --------------------------------------------------------------------------- #
# consign import (client-ledger rows)
# --------------------------------------------------------------------------- #
def test_exact_resighting_on_import_attaches_to_the_row_on_record(inv, capsys):
    path = ledger(inv, FLATWARE_LOT, GIFT_BAG)
    again = dict(bin="03", qty="1 set", photos="ZZ900301 ZZ900302",
                 description="Gift bag of flatware with white rope handles: long serving "
                             "pieces, ladle spoon, forks, black-handled knife")
    assert import_catalog(inv, [again]) == 0
    assert "added 0 row(s)" in capsys.readouterr().out

    rows = by_id(path)
    assert sorted(rows) == ["K01-C007", "K01-C008"]          # no K01-C009
    assert rows["K01-C008"]["photos"] == "ZZ900238 ZZ900301 ZZ900302"
    assert "re-run IDENTIFY" in rows["K01-C008"]["why"]
    assert rows["K01-C007"]["photos"] == FLATWARE_LOT["photos"]   # the lot is untouched
    q = read(inv / "CONSIGN-K01" / "dupe_queue.csv")
    assert [(r["action"], r["target"]) for r in q] == [("ATTACHED", "K01-C008")]

    # the same catalog imported again changes nothing
    assert import_catalog(inv, [again]) == 0
    assert by_id(path) == rows
    assert len(read(inv / "CONSIGN-K01" / "dupe_queue.csv")) == 1


def test_genuinely_new_item_on_import_gets_its_own_row(inv):
    path = ledger(inv, FLATWARE_LOT, GIFT_BAG)
    assert import_catalog(inv, [dict(bin="09", description="Cast brass post office box "
                                     "door, eagle front, combination dial",
                                     photos="ZZ900693")]) == 0
    rows = by_id(path)
    assert sorted(rows) == ["K01-C007", "K01-C008", "K01-C009"]
    assert rows["K01-C009"]["state"] == "INTAKE"
    assert not (inv / "CONSIGN-K01" / "dupe_queue.csv").exists()


def test_ambiguous_import_waits_in_the_queue_and_makes_no_row(inv, capsys):
    path = ledger(inv, AXE_A, AXE_B)
    assert import_catalog(inv, [dict(bin="05", photos="ZZ900839",
                                     description="Iron single-bit axe head, dug")]) == 0
    assert "queued 1 for review" in capsys.readouterr().out
    assert sorted(by_id(path)) == ["K01-C096", "K01-C100"]
    (line,) = read(inv / "CONSIGN-K01" / "dupe_queue.csv")
    assert line["action"] == "REVIEW"
    assert "K01-C096" in line["matches"] and "K01-C100" in line["matches"]
    assert line["photos"] == "ZZ900839"


def test_new_flag_on_import_forces_the_row_and_says_why(inv):
    path = ledger(inv, AXE_A, AXE_B)
    assert import_catalog(inv, [dict(bin="05", photos="ZZ900839",
                                     description="Iron single-bit axe head, dug")],
                          "--new", "a third head, wider poll") == 0
    rows = by_id(path)
    assert sorted(rows) == ["K01-C096", "K01-C100", "K01-C101"]
    assert "new row forced (--new" in rows["K01-C101"]["why"]
    assert "a third head, wider poll" in rows["K01-C101"]["why"]
    (line,) = read(inv / "CONSIGN-K01" / "dupe_queue.csv")
    assert (line["action"], line["target"]) == ("FORCED_NEW", "K01-C101")


# --------------------------------------------------------------------------- #
# tg7_triage add (catalog rows from camera triage and /intake)
# --------------------------------------------------------------------------- #
def test_walk_through_then_individual_shoot_attaches_to_the_ledger_row(inv, capsys):
    path = ledger(inv, OPENER, HARDWARE)
    d = lot(inv)
    add(d / "catalog.csv",
        "Letter opener, steel blade, scrimshaw-style handle with a harbour ship scene "
        "and lighthouse", "ZZ900649 ZZ900650 ZZ900651",
        "--folder", "inventory/CONSIGN-K01/K01-C022-letter-opener")
    assert capsys.readouterr().out.startswith("ATTACH K01-C022")

    row = by_id(path)["K01-C022"]
    assert row["photos"] == "ZZ900256 ZZ900649 ZZ900650 ZZ900651"
    assert row["inventory_folder"] == "inventory/CONSIGN-K01/K01-C022-letter-opener"
    assert "re-run IDENTIFY" in row["why"]
    assert by_id(path)["K01-C084"]["photos"] == "ZZ900256"
    assert not (d / "catalog.csv").exists()                 # no parallel row
    assert [p.name for p in d.iterdir()] == ["dupe_queue.csv"]


def test_genuinely_new_object_gets_a_catalog_row_and_folder(inv, capsys):
    path = ledger(inv, OPENER, HARDWARE)
    d = lot(inv)
    add(d / "catalog.csv", "Henckels paring knife, carbon blade, wood grip, three rivets",
        "ZZ900657 ZZ900658", "--why", "possible value")
    assert "NEW inventory/tg7-20991024-1526/catalog.csv#01" in capsys.readouterr().out

    (row,) = read(d / "catalog.csv")
    assert (row["no"], row["bin"], row["photos"], row["why"]) == (
        "01", "tg7-20991024-1526", "ZZ900657 ZZ900658", "possible value")
    folder = inv.parent / row["inventory_folder"]
    assert folder.parent == d and folder.name.startswith("01-henckels-paring-knife")
    assert "description: Henckels paring knife" in (folder / "intake.txt").read_text()
    assert sorted(by_id(path)) == ["K01-C022", "K01-C084"]  # the ledger is untouched


def test_triage_hint_alone_is_the_middle_band_and_goes_to_the_queue(inv, capsys):
    # The C022 / C109 case: triage saw a wood-handled knife and wrote "possible
    # duplicate of" the letter opener. Words alone don't match; the hint lifts it
    # into the middle band, so the inventory manager decides.
    ledger(inv, OPENER, HARDWARE)
    d = lot(inv)
    add(d / "catalog.csv", "Steel blade knife, wood handle, three rivets", "ZZ900657",
        "--hint", "possible duplicate of K01-C022")
    assert capsys.readouterr().out.startswith("REVIEW")
    assert not (d / "catalog.csv").exists()
    (line,) = read(d / "dupe_queue.csv")
    assert line["action"] == "REVIEW" and line["matches"].startswith("K01-C022")
    assert "triage hint" in line["matches"]


def test_two_close_rows_go_to_the_queue_and_new_forces_a_row(inv, capsys):
    ledger(inv, AXE_A, AXE_B)
    d = lot(inv)
    add(d / "catalog.csv", "Iron single-bit axe head, dug", "ZZ900839")
    assert capsys.readouterr().out.startswith("REVIEW")
    assert not (d / "catalog.csv").exists()

    add(d / "catalog.csv", "Iron single-bit axe head, dug", "ZZ900839",
        "--new", "wider poll than C096 and C100")
    (row,) = read(d / "catalog.csv")
    assert row["no"] == "01"
    assert "new row forced (--new" in row["why"] and "wider poll" in row["why"]
    assert [r["action"] for r in read(d / "dupe_queue.csv")] == ["REVIEW", "FORCED_NEW"]


def test_attach_flag_resolves_a_queued_candidate_to_the_row_named(inv):
    path = ledger(inv, AXE_A, AXE_B)
    d = lot(inv)
    add(d / "catalog.csv", "Iron single-bit axe head, dug", "ZZ900839",
        "--attach", "K01-C100")
    assert by_id(path)["K01-C100"]["photos"] == "ZZ900844 ZZ900839"
    assert by_id(path)["K01-C096"]["photos"] == "ZZ900835"


def test_a_resighting_within_the_same_bin_catalog_attaches(inv):
    d = lot(inv, "bin-garage-07")
    add(d / "catalog.csv", "Blue glass bud vase, about 6 in, ribbed neck", "P0001",
        "--bin", "bin-garage-07")
    add(d / "catalog.csv", "Ribbed blue glass bud vase", "P0009 P0010",
        "--bin", "bin-garage-07")
    (row,) = read(d / "catalog.csv")
    assert row["photos"] == "P0001 P0009 P0010"
    assert "re-run IDENTIFY" in row["why"]


def test_score_threshold_rules():
    rows = [dupe_guard.Row(Path("inventory/CONSIGN-K01/client_ledger.csv"), r)
            for r in (OPENER, HARDWARE)]
    same = dupe_guard.score({"description": OPENER["description"]}, rows[0])
    assert same.score >= dupe_guard.ATTACH
    folder = dupe_guard.score({"description": "anything",
                               "inventory_folder": "inventory/x/y"},
                              dupe_guard.Row(rows[0].source,
                                             dict(OPENER, inventory_folder="inventory/x/y")))
    assert folder.score == 1.0
    unrelated = dupe_guard.decide({"description": "Pyrex mixing bowl, yellow"}, rows)
    assert (unrelated.action, unrelated.matches) == ("new", [])
