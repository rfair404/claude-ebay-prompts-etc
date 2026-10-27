"""US-only routing tests.

The load-bearing test in this file is `test_periodicals_are_not_firearms`.
This inventory sells MAGAZINES — Esquire, Britches catalogs, periodical lots —
and a naive `\\bmagazine\\b` would route them to a firearms policy. Every
firearm pattern requires gun context for exactly that reason. If someone
loosens a pattern, that test is what should stop them.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from us_only import is_us_only, us_only_reasons  # noqa: E402


class D(dict):
    """Minimal stand-in for Draft: dotted-key .get()."""
    def get(self, key, default=None):  # noqa: D102
        return dict.get(self, key, default)


def mk(title="", extra=None, **kw):
    d = D({"title": title})
    if extra is not None:
        d["item_specifics.extra"] = extra
    d.update(kw)
    return d


# --------------------------------------------------------------- must route
def test_ruger_magazine_routes_us_only():
    """The item that forced this module into existence."""
    d = mk("RUGER Mini 14 Factory 5 Round Magazine 223 5.56 Blued Steel OEM Ranch Rifle")
    assert is_us_only(d)


def test_gun_aspect_alone_is_enough():
    """eBay's own required aspects are the strongest signal."""
    d = mk("Some Part", extra={"For Gun Type": "Rifle", "Number of Rounds": "5"})
    assert is_us_only(d)
    assert any("For Gun Type" in r for r in us_only_reasons(d))


def test_explicit_flag_wins_without_any_pattern():
    d = mk("A Perfectly Ordinary Teacup")
    d["shipping.us_only"] = "true"
    assert is_us_only(d)


def test_unbranded_part_by_caliber():
    assert is_us_only(mk("Blued Steel 5 Round Magazine .308 Winchester"))


def test_various_restricted_categories():
    for title in (
        "Vintage Colt 1911 Barrel .45 ACP",
        "AR-15 Lower Receiver Stripped",
        "Level III Ballistic Plate Carrier Body Armor",
        "Gen 2 Night Vision Scope",
        "Remington 870 12 Gauge Choke Tube",
        "Assorted Gun Parts Lot",
    ):
        assert is_us_only(mk(title)), title


def test_black_powder_revolver_parts_route_us_only():
    """K01-C137: a spare-parts card naming no single part, only the gun."""
    title = ("Richland Arms Nicky Line Spare Parts Fits 1851 Navy Army "
             "Black Powder Revolver")
    assert "firearm parts (US export-restricted)" in us_only_reasons(mk(title))
    for t in ("Revolver Parts Lot Hammer Springs Screws",
              "Pistol Spare Parts Kit",
              "Cap and Ball Replica Parts Set",
              "Muzzleloader Replacement Parts"):
        assert is_us_only(mk(t)), t


def test_gun_type_aspect_alone_is_enough():
    assert is_us_only(mk("Spare Parts Set", extra={"For Gun Type": "Revolver"}))
    assert is_us_only(mk("Spare Parts Set", extra={"Gun Type": "Revolver"}))


def test_generic_parts_need_a_firearm_type():
    """Brand names alone beside "parts" are not firearm context."""
    for title in (
        "Henry Ford Model T Car Parts Lot",
        "Vintage Singer Sewing Machine Parts Bobbin Case",
        "Black Powder Coated Steel Shelf Bracket Parts",
    ):
        assert not is_us_only(mk(title)), f"false positive: {title}"


# ----------------------------------------------------- must NOT route (!!!)
def test_periodicals_are_not_firearms():
    """The false-positive case this whole design is shaped around."""
    for title in (
        "Esquire Magazine Lot of 12 1979 1980 Mens Fashion Periodicals",
        "Vintage LIFE Magazine March 1969 Apollo 9 Cover",
        "Britches of Georgetowne Catalog Mailer 1983 Menswear",
        "National Geographic Magazine Bound Volume 1972",
        "Playbill Magazine Broadway 1988",
    ):
        assert not is_us_only(mk(title)), f"false positive: {title}"


def test_ordinary_inventory_is_not_restricted():
    for title in (
        "Vintage SPEIDEL USA Gold Tone Curb Link ID Bracelet Blank Plate 7 in Unisex",
        "Vintage Polaroid Automatic 320 Land Camera Cold Clip Strap Rangefinder 1969",
        "Vintage 1/20 12K Gold Filled Cross Pendant Necklace 24 in Curb Chain Satin",
        "Antique Cast Iron Skillet No. 8 Smooth Bottom",
        "Mid Century Teak Magazine Rack Danish Modern",
        "Sterling Silver Bolt Ring Clasp Findings Lot",
    ):
        assert not is_us_only(mk(title)), f"false positive: {title}"


def test_stock_and_barrel_need_gun_context():
    """'Stock' and 'barrel' are common antique words on their own."""
    for title in (
        "Antique Oak Barrel Stave Basket Primitive Farmhouse",
        "Vintage Stock Certificate Railroad 1923 Scripophily",
        "Wooden Barrel Butter Churn Dasher Antique",
    ):
        assert not is_us_only(mk(title)), f"false positive: {title}"


def test_reasons_are_human_readable():
    reasons = us_only_reasons(mk("Ruger Mini 14 5 Round Magazine"))
    assert reasons and all(isinstance(r, str) and len(r) > 10 for r in reasons)


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception:
            bad += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
