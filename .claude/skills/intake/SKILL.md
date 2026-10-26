---
name: intake
description: Catalog a bin or a group of items from photos so every piece is accounted for. The inventory-manager looks at every frame, numbers each distinct object, writes one catalog.csv for the bin, and gives each object its own item folder inside the bin (or a client_ledger.csv row for consignment). Use when Russell types /intake, or asks to "catalog this bin", "what's in this box", "log these" or "get these on the list".
argument-hint: "<photos folder or bin name> [K01] [$spend]"
---

# /intake — catalog a bin

Typing `/intake` is the ask, so run it without confirming first. The words
after it name the photos and, optionally, a client code (`K01`, consigned)
and what the bin cost (`$40`). Read loose wording generously. With no folder
named, use the newest frames: the latest `/r camera photos` pull in
`~/Pictures/TG-7`, or the newest `inventory/` folder that holds frames and no
`catalog.csv`.

This is a count, not a listing. Nothing here prices an item, names more than
the photos show, or stamps a SKU. Each object goes through the pipeline
later, one at a time, once Russell shoots it.

## Set up the bin folder

The bin is one folder holding the group frames:

- **Owned:** `inventory/<bin>/`, a short slug such as `bin-garage-07`.
- **Consigned:** `inventory/CONSIGN-<code>/<bin>/`, under the client's folder.

If the frames are in `~/Pictures/TG-7`, copy just this bin's frames in. For an
owned bin, record its cost and date with the context writer, from Russell's
words only (leave `--spend` off when he gave no cost; the source report then
flags the gap):

    python -m lib.cli context inventory/bin-garage-07 --kind event --spend 40 --acquired 2026-10-24

That makes the bin the cost bucket for everything inside it. Item folders
inside it get no `context.txt` of their own, so the bin's cost reaches their
sales (`lib/source_report.py`, nearest `context.txt` wins).

## Hand it off

Dispatch the `inventory-manager` agent (Agent tool, `run_in_background:
false`, since the reply is its list) with this brief:

- The bin folder (absolute path), its frames, and whether it's owned or
  consigned (client code only, never a name).
- Look at **every frame** at full size. Number each distinct object once,
  even when it shows in several frames: `01`, `02`, … in the order they
  first appear, left to right, front to back. Things that sell together (a
  cup and saucer, a pair, a boxed set) are one object with a `qty`.
- Write `<bin folder>/catalog.csv`, one row per object:

      no,bin,description,qty,marks,condition,photos,inventory_folder,why

  `description` says plainly what it is in a few words ("blue glass bud
  vase, ~6 in"). `marks` holds only text, stamps or labels legible in a
  frame, word for word. `condition` holds only damage visible in a frame.
  `photos` lists the frames it's in. `why` holds one flag, or stays blank:
  `possible value` (old, heavy, signed, marked sterling/14K, or anything
  Russell called out), `partly hidden` (it needs its own frame), or `check
  count` (a pile whose count is an estimate). A maker, era or material the
  frames don't show stays out.
- Make every row with `python tools/tg7_triage.py add <bin folder>/catalog.csv
  --description "..." --qty .. --marks ".." --condition ".." --photos ".."
  --why ".."`, never by writing the file by hand. It first checks the object
  against the rows already on record (this bin, the camera lots, the
  consignment ledgers). `NEW` makes the row; `ATTACH <key>` means the object
  is already on record and only its frames were added to that row; `REVIEW`
  leaves it in `<bin folder>/dupe_queue.csv`. Settle each REVIEW yourself
  with `--attach <key>` or `--new "<how it differs>"`, never by asking
  Russell.
- **Owned:** `add` makes one folder per object inside the bin,
  `<bin folder>/<no>-<slug>/` (e.g. `03-tin-toy-car/`), holding an
  `intake.txt` with its catalog row, and puts that path in
  `inventory_folder`. Russell's shoot frames for the item go in that folder
  later.
- **Consigned:** pass `--no-folder`, so `inventory_folder` stays blank. Item
  folders are made at the shoot; the catalog is the import file.
- Return the object count, the catalog path, the flagged rows and any
  ATTACH or REVIEW lines. Nothing else.

## After

- **Consigned:** run the import from this session. It writes only the
  client's `client_ledger.csv`, gives each row the client's next id, and
  skips a row it already has, so a re-run is safe:

      python -m lib.cli consign import CONSIGN-K01 inventory/CONSIGN-K01/<bin>/catalog.csv --client K01

  A row that is an object the client's ledgers already hold is attached to
  that row instead of added, and an unclear one is queued in
  `inventory/CONSIGN-K01/dupe_queue.csv`; the inventory manager settles
  those.

- **`possible value` rows** are held: each gets a closer look and exact
  comps before it has any price, never a placeholder.
- **`partly hidden` and `check count` rows:** one shot-list step on
  Russell's card covers them all ("pull 03, 07 and 11 out of the bin for one
  frame each").

## Reply

The count and one line per object, then the card if anything needs his
hands:

    Bin garage-07: 14 items catalogued.
    01 blue glass bud vase, ~6 in
    02 pair of brass candlesticks (qty 2) · possible value
    03 tin toy car · partly hidden
    …
    Catalog: inventory/bin-garage-07/catalog.csv

Leave out prices and client names.
