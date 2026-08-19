#!/usr/bin/env python3
"""Pop's Games mini thank-you cards -> print-ready PDF.

Small sibling of make_cards.py: the same off-register design squeezed onto a
2.000 x 1.250in landscape card, imposed as many per US Letter sheet as the
0.25in printable margin allows (32 at the default size). Corner ticks only,
so a trimmed card carries nothing but the design.

Two inks on bare paper and no flood coverage -- --mono drops the red plate to
a grey halftone for a black-only laser.

  python make_mini_cards.py
  python make_mini_cards.py --card 2x1.25 --store ebay.com/usr/popsgames
"""
import argparse
import os

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

import make_cards as mc
from make_cards import IN, INK, RED, GHOST, GREY, register_fonts

# ---- card ----------------------------------------------------------------
CARD_W = 2.000 * IN                  # 144pt
CARD_H = 1.250 * IN                  # 90pt

# The mono plate: a grey the laser halftones, so the off-register strike still
# reads as a second impression instead of a smudge of solid black on black.
TICK = HexColor("#7d7d7d")           # light enough to ignore, dark enough
                                     # for a laser to put down a hairline
GHOST_MONO = HexColor("#a8a8a8")
GREY_MONO = HexColor("#4a443c")

COLOR = {"ghost": GHOST, "rule": RED, "store": RED, "tag": GREY, "ink": INK}
MONO = {"ghost": GHOST_MONO, "rule": INK, "store": INK, "tag": GREY_MONO,
        "ink": INK}


def draw_card(c, ox, oy, name, tag, store, pal=COLOR, w=CARD_W, h=CARD_H):
    """Draw one mini card with its lower-left corner at (ox, oy).

    The 2in square card's ratios put the store line at 5pt once the card comes
    down to this size, so the small type gets its own scale here: the identity
    block is set near the width the card can actually hold, and the display
    type gives up the room to pay for it.
    """
    em = w / 16.0                                   # 9pt at 2in wide
    pad_t, pad_x, pad_b = 1.00 * em, 1.30 * em, 1.00 * em
    left = ox + pad_x

    # --- display type, top: "Thank / you." with the red plate behind ---
    ty = 1.70 * em
    leading = 0.92 * ty
    asc = pdfmetrics.getAscent("Georgia", ty)
    first = oy + h - pad_t + (leading - ty) / 2.0 - asc
    lines = [("Thank", "Georgia"), ("you.", "Georgia-Italic")]
    dx, dy = 0.05 * ty, -0.042 * ty                 # off-register offset
    for i, (text, font) in enumerate(lines):
        y = first - i * leading
        mc._line(c, left + dx, y + dy, text, font, ty, pal["ghost"],
                 tracking=-0.025 * ty)
        mc._line(c, left, y, text, font, ty, pal["ink"], tracking=-0.025 * ty)

    # --- identity block, bottom: rule, name, tagline, store ---
    gap = 0.30 * em
    y = oy + pad_b

    st_size = 0.78 * em                             # 7pt: the line a buyer
    mc._line(c, left, y + mc._baseline_from_box_bottom("CourierNew", st_size),
             store, "CourierNew", st_size, pal["store"], tracking=0.04 * st_size)
    y += 1.2 * st_size + gap                        # has to be able to type

    tg_size = 0.72 * em
    mc._line(c, left, y + mc._baseline_from_box_bottom("CourierNew", tg_size),
             tag, "CourierNew", tg_size, pal["tag"], tracking=0.14 * tg_size)
    y += 1.2 * tg_size + gap

    nm_size = 0.88 * em
    mc._line(c, left, y + mc._baseline_from_box_bottom("Georgia-Bold", nm_size),
             name.upper(), "Georgia-Bold", nm_size, pal["ink"],
             tracking=0.20 * nm_size)
    y += 1.2 * nm_size + gap

    c.setFillColor(pal["rule"])
    c.rect(left, y, 2.6 * em, 0.075 * em, stroke=0, fill=1)


# ---- sheet ---------------------------------------------------------------
PAGE_W, PAGE_H = letter                              # 612 x 792
MIN_MX, MIN_MY = 0.25 * IN, 0.40 * IN                # laser's printable edge


def impose(w, h):
    """As many whole cards as fit inside the printable area, centred."""
    cols = int((PAGE_W - 2 * MIN_MX) // w)
    rows = int((PAGE_H - 2 * MIN_MY) // h)
    if cols < 1 or rows < 1:
        raise SystemExit("card is too big for a Letter sheet")
    return cols, rows, (PAGE_W - cols * w) / 2.0, (PAGE_H - rows * h) / 2.0


def draw_ticks(c, cols, rows, mx, my, w, h):
    """Corner ticks in the margin, outside the block, aimed at each cut.

    Nothing at all is printed between the cards, so a trimmed card comes out
    carrying only the design -- the blade never crosses a guide.
    """
    c.setStrokeColor(TICK)
    c.setLineWidth(0.25)
    tick = 0.1 * IN
    block_w, block_h = cols * w, rows * h
    for i in range(cols + 1):
        x = mx + i * w
        c.line(x, my - tick, x, my)
        c.line(x, my + block_h, x, my + block_h + tick)
    for j in range(rows + 1):
        y = my + j * h
        c.line(mx - tick, y, mx, y)
        c.line(mx + block_w, y, mx + block_w + tick, y)


def sheet(path, name, tag, store, pal, w, h, ticks=True):
    cols, rows, mx, my = impose(w, h)
    c = canvas.Canvas(path, pagesize=letter)
    c.setTitle("Pop's Games mini thank-you cards - %d up" % (cols * rows))
    for row in range(rows):
        for col in range(cols):
            draw_card(c, mx + col * w, my + (rows - 1 - row) * h,
                      name, tag, store, pal, w, h)
    if ticks:
        draw_ticks(c, cols, rows, mx, my, w, h)
    c.showPage()
    c.save()
    return path, cols * rows


def single(path, name, tag, store, pal, w, h):
    c = canvas.Canvas(path, pagesize=(w, h))
    c.setTitle("Pop's Games mini thank-you card")
    draw_card(c, 0, 0, name, tag, store, pal, w, h)
    c.showPage()
    c.save()
    return path, 1


def parse_card(spec):
    try:
        w, h = (float(v) for v in spec.lower().split("x"))
    except ValueError:
        raise SystemExit("--card wants WIDTHxHEIGHT in inches, e.g. 2x1.25")
    return w * IN, h * IN


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--name", default="POP'S GAMES")
    p.add_argument("--tag", default="BUY · SELL · TRADE")
    p.add_argument("--store", default="ebay.com/usr/popsgames")
    p.add_argument("--card", default="2x1.25", help="inches, WIDTHxHEIGHT")
    p.add_argument("--outdir", default=os.path.dirname(os.path.abspath(__file__)))
    args = p.parse_args()

    w, h = parse_card(args.card)
    if 0.78 * (w / 16.0) < 6.0:
        print("warning: the store line lands under 6pt at this card size")

    register_fonts()
    out = args.outdir
    cols, rows, _, _ = impose(w, h)
    n = cols * rows
    made = [
        sheet(os.path.join(out, "thankyou-mini-%dup-ticks.pdf" % n),
              args.name, args.tag, args.store, COLOR, w, h),
        sheet(os.path.join(out, "thankyou-mini-%dup-ticks-mono.pdf" % n),
              args.name, args.tag, args.store, MONO, w, h),
        single(os.path.join(out, "thankyou-mini-card.pdf"),
               args.name, args.tag, args.store, COLOR, w, h),
    ]
    for f, count in made:
        print(os.path.basename(f), count, "up,", os.path.getsize(f), "bytes")


if __name__ == "__main__":
    main()
