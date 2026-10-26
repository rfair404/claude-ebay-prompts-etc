# Pop's Games — brand assets

Thank-you cards for the buy/sell/trade side of the business. Vintage toy-shop
register: flat spot inks, no gradients, no photography.

## The mark

| | |
|---|---|
| Ink (text) | `#141210` |
| Barn red (rule, store line) | `#a8322b` |
| Off-register plate | `#c0392f` |
| Tagline grey | `#4a443c` |
| Display face | Georgia / Georgia Italic |
| Name face | Georgia Bold, 0.26em tracking, all caps |
| Utility face | Courier New |

The card is built on a 16-unit grid: `1em = card width / 16`, so the design
holds at any size. At 2in that puts the base unit at 9pt.

## Print files

Everything is 2.000in square, vector, fonts embedded.

| File | What it is |
|---|---|
| `thankyou-offregister-20up.pdf` | The 2in card. 20 cards on US Letter, butted edge to edge, black crop marks in the margin only — nothing printed between cards. |
| `thankyou-offregister-20up-noguides.pdf` | No guides at all. |
| `thankyou-offregister-card.pdf` | Single card, 2in page. For a print shop or a different imposition. |

**Crop marks** (both sizes) work like a print shop's: short black hairlines in
the sheet margin, *outside* the card block, one aimed at every cut, stopping
1/32in short of the trim. Nothing is printed between the cards, so the blade
never crosses a guide and a trimmed card carries only the design.

Print at **100% scale** — "fit to page" shrinks the squares and the cut
spacing stops matching a trimmer. White is bare paper, so the stock is the
card color; nothing is flooded, which is why this design is cheap to run on
the color laser.

Sheet geometry: 4 across x 5 down, 0.25in side margins, 0.5in top and bottom.
Trimming is 3 vertical passes and 4 horizontal, plus the outer border.

## Mini cards

The same design on a **2.000 x 1.250in** landscape card, 32 to a US Letter
sheet (4 across x 8 down), built by `make_mini_cards.py`. The small type is
on its own scale rather than the square card's ratios — store line 7pt, name
7.9pt, tagline 6.5pt — because those ratios put the URL at 5pt once the card
comes down to this size. Toner coverage is about 3% of the sheet.

| File | What it is |
|---|---|
| `thankyou-mini-32up-ticks.pdf` | **The one to print.** Two inks, black type with the red plate. |
| `thankyou-mini-32up-ticks-mono.pdf` | Black-only — the off-register plate drops to a grey the laser halftones. For the mono printer, or to spend no color toner. |
| `thankyou-mini-card.pdf` | Single card, 2 x 1.25in page. For a print shop. |

Sheet geometry: 0.25in side margins, 0.5in top and bottom, cards butted edge
to edge. Trimming is 3 vertical passes and 7 horizontal, plus the outer
border. Print at **100% scale** — same rule as the 2in sheet.

Another size, re-imposed automatically to fit the sheet:

    python make_mini_cards.py --card 2.5x1.5

The type is on the card's own 16-unit grid, so it scales with the card; the
script warns when the store line falls under 6pt, which is where the URL
stops being comfortable to read.

## Listing photography

The cards above use no photography. Listings do, and these are the standing
rules for them. The art director owns them; PREP and REVIEW apply them.

### Hero rules

Photo one is eBay's gallery image. Most buyers see only that frame in search
and decide from it whether to click.

- **A scale or weighing shot is never the hero.** The hero shows the item
  itself. Scale frames are supporting proof of a weight claim, so they go
  after the item and detail shots, near the end of the order. *Why:* C085,
  the set of four salt spoons (junk store 880049733327), went live on
  2026-10-25 with the spoons on the scale as photo one. In search a buyer saw
  a kitchen scale and a blue readout, not the spoons.
- **Show the whole item, every piece of a set.** A set's hero shows every
  piece in it, clean and uncropped. A partial view (two spoons of four) is a
  detail shot, not a hero.
- **Show only what the buyer gets.** A frame that also shows other items
  (a group or intake shot) is never the hero, and it stays out of the
  listing, because a buyer reads everything in frame as included.
- **The default order:** hero, then marks and stamps, then condition
  details, then measurement and weight proof (ruler and scale frames).
- **Every number in a frame has to match the copy.** When a scale or ruler
  frame reads differently from the title or description, fix the text. Never
  swap the photo to suit the text.

## Regenerating

To change the eBay URL, the tagline, or the shop name:

    python make_cards.py --storefront-url ebay.com/usr/yourstore
    python make_mini_cards.py --storefront-url ebay.com/usr/yourstore

Requires `reportlab` and the Windows copies of Georgia and Courier New.

## Sources

- `proof-sheet-ten-designs.html` — the original ten concepts (sunburst,
  ticket, ribbon, prize seal, trade card, toy blocks, marquee, postmark,
  pennant, off-register). Kept for the rejected nine; any of them can be
  ported to `make_cards.py` the same way.
- `offregister-preview.html` — browser preview of the chosen design with
  live-editable fields.
