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

### Live Studio Resource center (read 2026-10-16)

`ebay.com/ebaylive/studio/resources` needs a signed-in eBay account. Our
account could read it but is **on the eBay Live waitlist**: Events,
Insights, Shipments and Settings are locked in Live Studio, and
`/ebaylive/studio` redirects to "You're on the waitlist". What it settles:

- **Ineligible listings** (`resources/itemid-listing`): Best Offer, reserve
  price, and Auction with Buy It Now. `live_flags` checks all three.
- **Buy It Now → auction happens in Stream manager** (`resources/lineup`).
  "Change to auction", alone or as a bulk action, converts a listing with a
  single quantity and a single SKU; multi-SKU and third-party-managed
  listings can't be converted. While it runs as an auction the listing has a
  **new Item ID**, with no insertion fee, and keeps the original listing's
  shipping policy. If it doesn't sell it returns to Buy It Now with its
  original Item ID and quantity. So the pipeline keeps listing Buy It Now;
  `live_notes` marks lots Stream manager can't convert.
- **Also in Stream manager:** edit title, description, price and auction
  duration; "Sudden death" (no timer extension); a "Purchasable" switch on
  Buy It Now lots; flash deals; pin (one item at a time; starting an
  auction pins it).
- **Live shipping policy** (`resources/shipping-policies`): at least one is
  required before adding listings. It's set up in Live Studio → Settings
  and policies, and it includes the combined-shipping discount.
- **OBS** (`resources/obs`, `resources/go-live`):
  - Advanced output, CBR 3000 Kbps, 1 s keyframe interval, x264
    `superfast`, audio 128 Kbps, 1080×1920 canvas and output at 30 FPS.
    Drop to 2000–2500 Kbps if frames drop.
  - Needs 30+ Mbps down and 10+ Mbps up.
  - Two modes: **No delay (WHIP)** for shows of about 1–3 h, which needs
    only the stream URL with no bearer token, and **High resolution
    (RTMP)** for 3 h+, which needs a URL and a key.
  - The stream URL stays the same across events, while the stream key
    changes for each event. The checked-in `Ebay` profile already matches
    all of this (`tools/live/obs/profiles/Ebay`).
  - OBS "Start Streaming" only sends video. The show starts at Stream
    manager → **Start event** (with an optional **Test connection**) and
    ends at **End event**. Recommended length: at least an hour.

## What's built

| Piece | File | eBay calls |
|---|---|---|
| Lineup builder | `tools/live_lineup.py` | Inventory API reads only |
| Lineup/state logic, pages | `lib/live_show.py` | none |
| Host page, overlay, state | `webapp/server.py` `/live/*` | none |
| Promo image (marquee) | `tools/live_marquee.py`, `lib/live_marquee.py` | Browse API reads only |

Show data lives in `inventory/_live/<show>/` (gitignored): `lineup.json`,
`item_ids.txt` (the paste list), `state.json` (the lot on screen), and the
marquee's `marquee.json` / `marquee.png` / `marquee_img/`.

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
ACTIVE, has Best Offer on, has a reserve price, or is an auction with a Buy
It Now price. An unflagged lot can still be refused by Stream manager.
Lots that can't be converted with "Change to auction" (quantity > 1, or
several SKUs on one listing) get a **note**, not a flag.

To paste flagged lots anyway (to see what Stream manager does with them),
rebuild with `--keep-flagged`. Best Offer is fixed by `--confirm` (see
"Show prep" below). Other flags mean editing the listing by hand.

Offers that share an Item ID (variation listings) become one lot, so the
lot count can be lower than the active-offer count.

Measured 2026-10-13: the **default** store had 238 active offers, giving
230 lots, 163 of them flagged for Best Offer. The **junk** store had 38
active lots and none were flagged.

## Marquee

A 1050x1575 promo image for the show: title, start time, offer badges,
four hero photos and up to 21 thumbnails, with an optional corner ribbon
("FIRST SHOW!"). `--pick` sweeps the seller's live listings in one category
(Jewelry & Watches by default) through Browse, keeps the oldest that pass
the filters, and downloads their first photos:

    python tools/live_marquee.py --show jewelry-oct-14 --pick --limit 25 \
        --min-price 25 --max-price 100 --skip-seasonal \
        --exclude 206418132464 --include 206418132358 --start "2026-10-14 20:00"

Watch parts, bands and brochures filed under Jewelry & Watches are always
dropped. `--include` adds pieces filed elsewhere (religious medals under
Collectibles, say); they still face the price and seasonal filters.

Then edit `marquee.json` and run again without `--pick` to re-render:
labels, `heroes`, `deck` (`{n}` becomes the item count), ribbon and footer
copy, and per item `photo_url` (another of the listing's photos, when the
first one is mostly background at thumbnail size), `focus` (CSS
object-position) and `zoom`. `--pick` keeps all of those for items still on
the list. A photo is downloaded again when the listing's photo URL changes,
so re-shot listings show their new photo. Thumbnail rows are balanced and
centered (16 items lay out 6/6/4).

`--start` is Eastern. The tool also prints that moment in GMT, which is
what eBay's show scheduler showed on 2026-10-13. In daylight time an 8 PM
ET show is 00:00 GMT **the next day**.

The marquee only advertises. It doesn't make the listings $1 auctions with
free shipping, and it doesn't turn Best Offer off. Most of the oldest
default-store jewelry has Best Offer on, which `live_lineup.py` flags.

## Known limits

- The Inventory API can't see listings made in the eBay web UI, so neither
  mode finds them. Paste those IDs into Stream manager by hand.
- `--items` resolves through the listings ledger. IDs the ledger doesn't
  know (it was rebuilt in October) are found by walking every offer on the
  account (8 calls at a time; ~25 s on the default store's 368 SKUs).
- Both pages are localhost-only, like the rest of the app. OBS has to run
  on the same machine as `serve`.

## Show prep: `tools/live_prep.py`

One command finds the show's items, makes them fit eBay Live's listing
rules, and writes the paste list and the marquee:

    python tools/live_prep.py --store default --show jewelry-oct-20 \
        --start "2026-10-20 20:00" --limit 25 --min-price 25 --max-price 100 \
        --skip-seasonal                       # dry run: nothing on eBay changes
    python tools/live_prep.py ... --confirm   # turns Best Offer off on the picks

1. **Find.** It sweeps the seller's live listings in `--category` (default
   281, Jewelry & Watches) through Browse, oldest first. The filters are the
   same as the marquee's `--pick`. `--seller` defaults to the default
   store's username; any other store needs it passed.
2. **Check.** It reads every active offer on the store (Inventory API) and
   runs each candidate through `live_flags`.
3. **Choose.** It keeps candidates that comply, or that only need Best
   Offer turned off, until `--limit`. The rest are skipped and the output
   says why: a reserve price, Buy It Now on an auction, or not visible to
   the Inventory API (made in the eBay web UI).
4. **Fix.** With `--confirm`, it turns Best Offer off on the chosen
   listings. Each offer is re-read and PUT back with only `bestOfferTerms`
   removed. Its old terms are saved on the lot as `best_offer_was`.
5. **Write.** `lineup.json` and `item_ids.txt`, then `marquee.json`, and
   it renders the marquee. `--no-marquee` skips the marquee.

After the show, put Best Offer back on whatever didn't sell:

    python tools/live_lineup.py --store default --show jewelry-oct-20 \
        --restore-best-offer [--confirm]

This restores the saved terms on listings that are still ACTIVE Buy It Now
under the same Item ID. Sold or ended listings drop their record. A listing
still running as a converted auction keeps it, for a later run. Rebuilding
a lineup that would drop a lot with a pending restore is refused, so the
record can't be lost.

`tools/live_lineup.py` does the same check and fix for a hand-picked list:
`--items`, `--all`, or `--from-marquee` (the marquee's items), with
`--confirm` to apply the Best Offer fix.

Still manual, in Live Studio:

- create the event
- set up a Live shipping policy
- paste `item_ids.txt` into Stream manager → Add listings → By Item ID/URL
- "Change to auction" on the lots you want auctioned

### Start time and the pick-list hold

- **`--start`** is the event's scheduled start in Live Studio, in Eastern
  time unless the value carries an offset. It's stored in `lineup.json` as
  UTC, and later builds keep it.
- **Pick-list hold.** eBay keeps a show's orders in "Paid – wait to ship"
  until 8 h after the scheduled start, and shipping early stops combined
  shipping. `tools/pick_list.py --poll` therefore holds an order while that
  window is open if either:
  - it has a lot from the lineup, or
  - it was placed after the scheduled start. This rule catches converted
    auctions, which sell under a new Item ID.

  Held orders aren't rendered or marked printed, so the first poll after
  the window closes picks them up together with the rest of that buyer's
  box. A show with no `--start` holds nothing.

## Not built: open questions to answer in the eBay UI first

- ~~What the stream URL is~~: WHIP (URL only) or RTMP (URL and key). The
  URL is the same for every event; the key changes per event. See above.
- Whether an imported 7/10-day auction gets a short live timer, and what
  happens to bids it already has. (Stream manager can edit an auction's
  duration, per `resources/lineup`.)
- Whether Item IDs can be pasted while a show is live, and how fast the
  item appears.
- Whether listings created inside Live Studio show up in our API reads
  afterwards.

These decide whether the next step is "camera → identify → publish → paste
mid-show". Until they're answered, building it would be guessing.
