# eBay Live — MVP integration

MVP, built 2026-10-13. Two things work, and both were checked against the
real account: building a show's lineup from our own listings, and a host
page that drives an OBS overlay. Nothing here publishes, edits or ends a
listing.

## What eBay gives us (researched 2026-10-13)

eBay Live has **no public API**. It isn't in the developer API catalog or
the Sell API release notes. The Notification API's topic list, queried with
this app's own credentials, has 27 topics and none of them is about Live.
The only ones that matter for a show are `ORDER_CONFIRMATION` and `LISTING`.

The seller help page
([id=5762](https://www.ebay.com/help/selling/ebay-live/livestream-selling-ebay-live?id=5762))
documents these seams:

- **Lineup:** in Live Studio or Stream manager, *Add listings → By Item
  ID/URL* takes pasted Item IDs, up to 750 per event.
- **Formats:** "Auction and Buy It Now listings can be added by all sellers."
- **Rules:** "Listings can't include any special pricing options like Best
  Offer or reserve pricing."
- **Video:** from the eBay app, or from a broadcast tool such as OBS using
  the event's stream URL.

## What's built

| Piece | File | eBay calls |
|---|---|---|
| Lineup builder | `tools/live_lineup.py` | Inventory API reads only |
| Lineup/state logic, pages | `lib/live_show.py` | none |
| Host page, overlay, state | `webapp/server.py` `/live/*` | none |

Show data lives in `inventory/_live/<show>/` (gitignored): `lineup.json`,
`item_ids.txt` (the paste list) and `state.json` (the lot on screen).

## Demo

From the main checkout (config and ledgers live there; from a worktree set
`EBAYBIZ_CONFIG=<main>/config.yaml`):

    python tools/live_lineup.py --store junk --show oct-20 --all
    python tools/live_lineup.py --store junk --show oct-20 --items 880009332259 880005817739
    python tools/live_lineup.py --store junk --show oct-20 --append --items <new id>   # mid-show
    python -m lib.cli serve

1. Paste `inventory/_live/<show>/item_ids.txt` into Stream manager → Add
   listings → By Item ID/URL.
2. Open `http://127.0.0.1:8770/live/<show>`, the host page. It shows the
   lots in order, flags, price, Copy ID, and a link to each lot's review card.
3. In OBS add a **Browser** source, URL
   `http://127.0.0.1:8770/live/<show>/overlay`, 1920×1080. The page
   background is transparent; it shows a lower-third with the lot number,
   title and the host's line, and polls once a second.
4. Click **Show** on a lot, or type a line ("Bidding opens at $5") and
   **Set line**. The overlay follows. **Clear screen** hides it.

### Flags

A lot is flagged, and left out of `item_ids.txt` unless you pass
`--keep-flagged`, when it breaks one of eBay's documented rules: it isn't
ACTIVE, has Best Offer on, or has a reserve price. Nothing else is checked.
An unflagged lot can still be refused by Stream manager.

To paste flagged lots anyway (to see what Stream manager does with them),
rebuild with `--keep-flagged`. Clearing a flag means editing the live
listing (e.g. turning Best Offer off), which this tool never does. Note
that `lib/list_edit.py --update --fields bestoffer` works from a draft.md,
and listings published before the October rebuild have none.

Offers that share an Item ID (variation listings) become one lot, so the
lot count can be lower than the active-offer count.

Measured 2026-10-13: the **default** store had 238 active offers, giving
230 lots, 163 of them flagged for Best Offer. The **junk** store had 38
active lots and none were flagged.

## Known limits

- The Inventory API can't see listings made in the eBay web UI, so neither
  mode finds them. Paste those IDs into Stream manager by hand.
- `--items` resolves through the listings ledger. IDs the ledger doesn't
  know (it was rebuilt in October) are found by walking every offer on the
  account (8 calls at a time; ~25 s on the default store's 368 SKUs).
- Both pages are localhost-only, like the rest of the app. OBS has to run
  on the same machine as `serve`.

## Not built: open questions to answer in the eBay UI first

- What the stream URL is: the protocol (RTMP/SRT/WHIP), and whether the
  key is per event.
- Whether an imported 7/10-day auction gets a short live timer, and what
  happens to bids it already has.
- Whether Item IDs can be pasted while a show is live, and how fast the
  item appears.
- Whether listings created inside Live Studio show up in our API reads
  afterwards.

These decide whether the next step is "camera → identify → publish → paste
mid-show". Until they're answered, building it would be guessing.
