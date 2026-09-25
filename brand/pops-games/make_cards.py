#!/usr/bin/env python3
"""Pop's Games thank-you cards -> print-ready PDF.

Vector output, fonts embedded, cards drawn at an exact 2.000in square so a
paper trimmer lands on the cut lines. Mirrors the off-register design
(black type with a red plate struck a hair out of alignment) on bare white,
which is what the color laser wants: two inks, no flood coverage.

  python make_cards.py --store ebay.com/usr/popsgames
"""
import argparse
import os

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# ---- units ---------------------------------------------------------------
IN = 72.0
CARD = 2.0 * IN                 # 144pt
EM = CARD / 16.0                # 9pt: the design's base unit

INK = HexColor("#141210")
RED = HexColor("#a8322b")
GHOST = HexColor("#c0392f")     # the misregistered plate, a touch brighter
GREY = HexColor("#4a443c")
CROP = HexColor("#000000")      # crop marks: solid black, margin only
CROP_GAP = 0.03 * IN            # clear space between trim and mark
CROP_LEN = 0.07 * IN            # ends 0.15in from the sheet edge

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FACES = {
    "Georgia": "georgia.ttf",
    "Georgia-Italic": "georgiai.ttf",
    "Georgia-Bold": "georgiab.ttf",
    "CourierNew": "cour.ttf",
}


def register_fonts():
    for name, filename in FACES.items():
        pdfmetrics.registerFont(TTFont(name, os.path.join(FONT_DIR, filename)))


def _baseline_from_box_bottom(font, size):
    """CSS line box is 1.2em; recover where the baseline sits inside it."""
    half_leading = 0.1 * size
    descent = abs(pdfmetrics.getDescent(font, size))
    return half_leading + descent


def _line(c, x, y, text, font, size, color, tracking=0.0):
    t = c.beginText(x, y)
    t.setFont(font, size)
    t.setFillColor(color)
    t.setCharSpace(tracking)
    t.textOut(text)
    c.drawText(t)


def draw_card(c, ox, oy, name, tag, store):
    """Draw one 2in card with its lower-left corner at (ox, oy)."""
    pad_t, pad_x, pad_b = 1.55 * EM, 1.35 * EM, 1.30 * EM
    left = ox + pad_x

    # --- display type, top: "Thank / you." with the red plate behind ---
    ty = 2.05 * EM                                  # 18.45pt
    leading = 0.92 * ty                             # line-height .92
    asc = pdfmetrics.getAscent("Georgia", ty)
    first = oy + CARD - pad_t + (leading - ty) / 2.0 - asc
    lines = [("Thank", "Georgia"), ("you.", "Georgia-Italic")]
    dx, dy = 0.05 * ty, -0.042 * ty                 # off-register offset
    for i, (text, font) in enumerate(lines):
        y = first - i * leading
        _line(c, left + dx, y + dy, text, font, ty, GHOST, tracking=-0.025 * ty)
        _line(c, left, y, text, font, ty, INK, tracking=-0.025 * ty)

    # --- identity block, bottom: rule, name, tagline, store ---
    gap = 0.36 * EM
    y = oy + pad_b

    st_size = 0.56 * EM
    _line(c, left, y + _baseline_from_box_bottom("CourierNew", st_size),
          store, "CourierNew", st_size, RED, tracking=0.04 * st_size)
    y += 1.2 * st_size + gap

    tg_size = 0.56 * EM
    _line(c, left, y + _baseline_from_box_bottom("CourierNew", tg_size),
          tag, "CourierNew", tg_size, GREY, tracking=0.20 * tg_size)
    y += 1.2 * tg_size + gap

    nm_size = 0.62 * EM
    _line(c, left, y + _baseline_from_box_bottom("Georgia-Bold", nm_size),
          name.upper(), "Georgia-Bold", nm_size, INK, tracking=0.26 * nm_size)
    y += 1.2 * nm_size + gap

    c.setFillColor(RED)
    c.rect(left, y, 2.6 * EM, 0.075 * EM, stroke=0, fill=1)


# ---- sheet ---------------------------------------------------------------
COLS, ROWS = 4, 5
PAGE_W, PAGE_H = letter                              # 612 x 792
MX = (PAGE_W - COLS * CARD) / 2.0                    # 18pt  = 0.25in
MY = (PAGE_H - ROWS * CARD) / 2.0                    # 36pt  = 0.50in


def draw_guides(c, mode):
    """Printer's crop marks: black, in the margin only, aimed at each cut.

    Nothing is printed between the cards, so a trimmed card carries only the
    design. Each mark stops CROP_GAP short of the trim so a blade that lands a
    hair outside the line still leaves no ink on the card edge.
    """
    if mode == "none":
        return
    c.setStrokeColor(CROP)
    c.setLineWidth(0.25)
    block_w, block_h = COLS * CARD, ROWS * CARD
    near, far = CROP_GAP, CROP_GAP + CROP_LEN
    for i in range(COLS + 1):
        x = MX + i * CARD
        c.line(x, MY - far, x, MY - near)
        c.line(x, MY + block_h + near, x, MY + block_h + far)
    for j in range(ROWS + 1):
        y = MY + j * CARD
        c.line(MX - far, y, MX - near, y)
        c.line(MX + block_w + near, y, MX + block_w + far, y)


def sheet(path, name, tag, store, mode):
    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle("Pop's Games thank-you cards - 20 up")
    for row in range(ROWS):
        for col in range(COLS):
            draw_card(c, MX + col * CARD,
                      MY + (ROWS - 1 - row) * CARD, name, tag, store)
    draw_guides(c, mode)
    c.showPage()
    c.save()
    return path


def single(path, name, tag, store):
    c = canvas.Canvas(path, pagesize=(CARD, CARD))
    c.setTitle("Pop's Games thank-you card - 2in")
    draw_card(c, 0, 0, name, tag, store)
    c.showPage()
    c.save()
    return path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", default="POP'S GAMES")
    p.add_argument("--tag", default="BUY \u00b7 SELL \u00b7 TRADE")
    p.add_argument("--store", default="ebay.com/usr/popsgames")
    p.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    args = p.parse_args()

    register_fonts()
    out = args.outdir
    made = [
        sheet(os.path.join(out, "thankyou-offregister-20up.pdf"),
              args.name, args.tag, args.store, "crop"),
        sheet(os.path.join(out, "thankyou-offregister-20up-noguides.pdf"),
              args.name, args.tag, args.store, "none"),
        single(os.path.join(out, "thankyou-offregister-card.pdf"),
               args.name, args.tag, args.store),
    ]
    for f in made:
        print(os.path.basename(f), os.path.getsize(f), "bytes")


if __name__ == "__main__":
    main()
