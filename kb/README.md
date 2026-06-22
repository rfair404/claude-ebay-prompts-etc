# Knowledge Base — cross-category collectables reference

A local, **prompt-readable** reference library of collectables knowledge that
any phase of a run can consult on demand. Where a
[specialization](../specializations/README.md) is a deep field guide for **one
category** (marbles, jewelry…), the KB holds the **cross-cutting** knowledge
that applies across many categories — hallmark systems, condition-grading
conventions, reproduction/fake red flags, where to find real comps, dating
clues, shipping/handling for fragile classes — plus the curated **resource
registry** the modules and prompts link out to.

Like the rest of v3 this is prompt-driven: an article is just a Markdown file
the pipeline reads when it's relevant. No code, no build step.

- **KB article** → reusable reference knowledge (a digest of one or more
  resources), category-agnostic or spanning categories.
- **Specialization** → one category's expert playbook (taxonomy, money types,
  price tiers). Lives in [`../specializations/`](../specializations/).

When the two overlap, the specialization wins for its category (it's more
specific); the KB is the fallback/shared layer and the place general knowledge
lives so it isn't copy-pasted into every module.

---

## How a run uses the KB (the contract)

1. **Consult on a cue, not always.** IDENTIFY / INVESTIGATE / PRICE check the
   article index below when an item raises a cross-category question a
   specialization doesn't already answer — e.g. "what does this silver
   hallmark mean?", "is this a reproduction?", "what's the right comp source
   for this?". Load the matching article; don't pre-load the whole KB.
2. **Refine, never override honesty.** KB articles obey the same rules as
   everything else: they *inform* a call, they do not license inventing.
   Maker-attribution discipline ([`../prompts/identify.md`](../prompts/identify.md)),
   the `[BEST-CASE]`/`[ASSUMPTION]` markers, and the fresh-investigation rule
   in [`../prompts/_shared.md`](../prompts/_shared.md) all still apply. Judge
   THIS item on its own evidence; use the article to recognize and interpret,
   not to import a prior conclusion.
3. **Dated and sourced.** Every article cites its source class and records
   `last_reviewed`, so stale market/marks claims can be refreshed. Live PRICE
   still hunts real comps — KB price signals are context, never quotes.
4. **Verify against real references on value-moving calls.** When an article
   points at a primary source (a hallmark date-letter chart, a real-vs-repro
   photo set), WebFetch it and compare before committing a value-tier call —
   same discipline as the specialization modules.
5. **New-source policy — surface, don't self-add.** Any time a run encounters a
   web source that isn't already in the registry below, **consider it for the
   KB and ASK the user before adding it.** Bring the URL, what it's good for,
   and why it might earn a place; let the user approve before it goes in the
   registry or gets ingested. Don't silently expand the KB mid-run. (Always
   record the **reference URL** with any fact taken from a source, so it can be
   cited correctly.)

> **Access note.** Some quality sources block automated fetching (HTTP 403 to
> WebFetch) — e.g. **925-1000.com**, **collect.guide**. For these, ingest via
> the **Claude-in-Chrome** extension (drive a real browser) or read them
> manually; never let a 403 silently drop a source. Mark such rows "Chrome to
> ingest" in the registry.

---

## Article index (what's ingested)

| Article | Covers | Status |
|---|---|---|
| [silver-hallmarks.md](articles/silver-hallmarks.md) | Sterling vs coin vs plate vs not-silver; reading US maker marks & British hallmark sets (standard/town/date-letter/maker); continental fineness; `925` forgery & EPNS red flags | v1 |
| [ebay-sold-comps.md](articles/ebay-sold-comps.md) | Finding real sold-price comps: sold≠asking, the Apify/Chrome ladder, query craft, delivered basis, distribution tiers, comp pitfalls | v1 |

_Add a row per article as it's ingested. Keep this in sync with `articles/`._

---

## Resource registry (the links section)

The curated, reputable sources the KB ingests from and that prompts may link
out to live. Grouped by **what they're good for**. Prefer specialist
societies, established auction houses, and standard references over random
listings — same source-quality bar as the specializations.

> Ingestion status legend: **▢ not started · ◐ partial · ● ingested** (digested
> into an `articles/` file). The goal is to turn each high-value resource into a
> reusable article so a run doesn't have to re-fetch and re-read the raw page.

### General collectables hubs & guides (cross-category identification + overview)
| Resource | Good for | URL | Ingest |
|---|---|---|---|
| COLLECT.Guide | Cross-category ID/reference hub: manufacturer rosters, size-for-ID, grading, glossaries, per-type guides (marbles, etc.) | https://www.collect.guide | ▢ retry — site down 2026-06-21 (Cloudflare 520 origin error); WebFetch also 403. Ingest via Chrome once origin is back |
| Collectors Weekly | Broad category overview guides + community knowledge | https://www.collectorsweekly.com/ | ▢ |
| The Spruce Crafts — Collectibles | Beginner-friendly category primers across many collectables | https://www.thesprucecrafts.com/collectibles-4127771 | ▢ |
| Invaluable — "Knowledge Center" | Auction-house-backed category guides + realized prices | https://www.invaluable.com/ | ▢ |

### Niche catalogs & databases (authoritative per-category references)
| Resource | Good for | URL | Ingest |
|---|---|---|---|
| Discogs | Music releases (vinyl/CD) — definitive catalog + sold marketplace data | https://www.discogs.com/ | ▢ |
| Numista | World coins & banknotes catalog with mintage/values | https://en.numista.com/ | ▢ |
| Colnect | Broad collectibles catalog (stamps, coins, banknotes, postcards, more) | https://colnect.com/ | ▢ |
| PSA / PCGS / Beckett | Third-party grading + population/price data (cards & coins) | https://www.psacard.com/ · https://www.pcgs.com/ | ▢ |
| BrickLink | LEGO sets & parts catalog + market prices | https://www.bricklink.com/ | ▢ |

### Comps & realized prices (what things actually sell for)
| Resource | Good for | URL | Ingest |
|---|---|---|---|
| eBay — Sold/Completed listings | The primary comp source; real sold prices across every category | https://www.ebay.com/sch/ (filter: Sold items) | ● [→](articles/ebay-sold-comps.md) |
| WorthPoint | Deep historical sold-price database (subscription); marks dictionary | https://www.worthpoint.com/ | ▢ |
| LiveAuctioneers | Aggregated auction-house realized prices, many categories | https://www.liveauctioneers.com/ | ▢ |
| Heritage Auctions (HA.com) | High-end realized prices + reference archive (coins, comics, art, watches) | https://www.ha.com/ | ▢ |
| Morphy Auctions | Realized prices for toys, advertising, glass, marbles, militaria | https://www.morphyauctions.com/ | ▢ |
| Replacements, Ltd. | China / crystal / silver / flatware **pattern** identification + going prices | https://www.replacements.com/ | ▢ |

### Identification, marks & makers
| Resource | Good for | URL | Ingest |
|---|---|---|---|
| Kovels | The standard antiques/collectibles marks + price guide; pottery/silver marks | https://www.kovels.com/ | ▢ |
| 925-1000.com | Encyclopedia of silver & sterling **hallmarks** (intl. date letters, makers) | https://www.925-1000.com/ | ● [→](articles/silver-hallmarks.md) |
| Marks4Antiques / online marks DBs | Pottery, porcelain & silver mark lookup | https://www.marks4antiques.com/ | ▢ |
| Gemological Institute of America (GIA) | Gemstone ID, grading standards, treatment disclosure | https://www.gia.edu/ | ▢ |

### Authentication & reproductions (don't get fooled)
| Resource | Good for | URL | Ingest |
|---|---|---|---|
| Real Or Repro | Side-by-side genuine-vs-reproduction tells across many categories | https://www.realorrepro.com/ | ▢ |
| Antique Trader — fakes coverage | Editorial on fakes, repros, and market trends | https://www.antiquetrader.com/ | ▢ |
| Entrupy / brand authentication (luxury) | Authentication context for handbags, sneakers, luxury goods | https://www.entrupy.com/ | ▢ |

### Community peer-ID forums (second opinion — crowd, not authority)
| Resource | Good for | URL | Ingest |
|---|---|---|---|
| Marble Connection — "Marble I.D.'s" | Marble photo-ID forum (~17k threads). Indexed for **visual similarity search** — given a reference marble, find the most-similar posted marbles. See [`../lib/marble_index.py`](../lib/marble_index.py) | https://marbleconnection.com/forum/22-marble-ids/ | ● (CLIP index + refresh tool) |

> Forums are **peer opinion** (sometimes wrong), so they're a *second-opinion /
> look-alike* layer, not an authority like MCSA or an auction house. The visual
> index narrows the corpus to candidates; final ID still follows the
> [marbles specialization](../specializations/marbles.md) discipline.

### Category specialist references (cross-link to specializations)
| Resource | Good for | URL | Ingest |
|---|---|---|---|
| Marble Collectors Society of America | Marbles — see [`../specializations/marbles.md`](../specializations/marbles.md) | https://www.marblecollecting.com/ | ● (in module) |
| (add specialist societies as categories are added) | | | |

_When a resource is specific to one category that already has a specialization
module, keep its deep links in that module; list it here only as a pointer._

---

## Tools (KB-adjacent scripts)

Reusable scripts a run can call to turn a source into an answer. Code is
tracked; generated indexes live under `kb/index/` (gitignored, regenerable).

### `lib/marble_index.py` — marble visual-similarity search

Index the marble photos posted to the Marble Connection "Marble I.D.'s" forum,
then find the threads whose marbles look most like a reference photo.

```
# build / extend the index (newest threads first; resumable)
python lib/marble_index.py index --max-pages 10 [--start-page 1]

# periodic sync — append only threads created since the last run
python lib/marble_index.py refresh [--max-new-pages 10]

# find the top-K most-similar threads for one or more reference photos
python lib/marble_index.py query path/or/url [more-angles...] --top 5 [--json out.json]

python lib/marble_index.py status      # index size + last sync
```

- **Embedding backend** (`MARBLE_EMBED` env): `phash` (default — colour+structure
  features, no torch) or `clip` (CLIP ViT-B/32, needs torch + the MS VC++
  Redistributable). An index is tied to the backend that built it; to upgrade
  phash→clip, install VC++ then rebuild with `MARBLE_EMBED=clip`.
- **Honesty contract:** similarity retrieval is a *candidate finder*, not an ID.
  Treat the top-K as "look here", then settle maker/era per the
  [marbles specialization](../specializations/marbles.md) (method ≠ origin ≠ era).
- **Periodic refresh:** `refresh` is safe to run on a schedule (idempotent,
  dedups by thread-id + image-URL) to keep the index current as new IDs are posted.

### `lib/ebay_visual.py` — eBay SOLD visual comp finder

Same featurizer as `marble_index` (imported, not duplicated), but the corpus is
**eBay sold listings with realized prices** — so a reference photo returns
visually-similar *sold* listings **and what they went for**. A priced
look-alike finder that complements the forum index (which has no prices).

Data flow — the Apify scraper is an **MCP tool the assistant runs**, not Python:
```
# 1) assistant runs automation-lab/ebay-sold-scraper for some pattern queries
#    (the same actor PRICE uses), then pulls the dataset to a JSON file, e.g.
#    via the Apify API using apify.api_token from config.yaml.
# 2) ingest those listings (download image, embed, dedup by itemId):
python lib/ebay_visual.py add --from results.json [more.json] --label "pattern-name"

# 3) find the top-K similar SOLD listings + price signal (min/median/max):
python lib/ebay_visual.py query path/or/url [more-angles...] --top 6 [--json out.json]

python lib/ebay_visual.py status
```

- **Index:** `kb/index/ebay-sold/` (gitignored). meta rows carry `price`, `url`,
  `soldDate`, `condition` → results are priced comps.
- **Honesty contract:** similarity finds *priced candidates*, not a valuation.
  The top-K are comps to **vet** — condition, size, and authenticity still
  decide validity (see [`../prompts/price.md`](../prompts/price.md)). Backend is
  `phash` today (CLIP upgrade is the same VC++ path as marble_index).
- **Build it by pattern:** ingest one named collectable pattern at a time
  (`--label`) so the library grows into a tagged, queryable set of priced
  examples for the categories you sell.

---

## Article file schema

Every article follows [`_template.md`](_template.md) so they stay
interchangeable and prompt-friendly. Required: front matter (`topic`,
`applies_to`, `version`, `last_reviewed`, `sources`), a short **When to consult
this**, the digested **knowledge** itself (tables/checklists a run can act on),
**Red flags / gotchas**, **How it maps onto our fields** (IDENTIFY/INVESTIGATE/
PRICE hooks), and **Sources**.

Keep articles **action-oriented**: a run should be able to *do* something with
each section (interpret a mark, grade a defect, pick a comp source), not just
read prose.

---

## Adding / ingesting an article

1. Pick a resource from the registry (start with the highest-leverage:
   comps + marks + repro).
2. Copy `_template.md` to `articles/<topic>.md`.
3. WebFetch the resource and **digest** it into the article's knowledge
   sections — durable, reusable facts (how a hallmark system works, the repro
   tells for a class), not a copy of the page. Cite the source and set
   today's `last_reviewed`.
4. Flip the resource's Ingest marker to ● and add the article to the index.
5. That's it — prompts pick it up via the index on the next relevant run.
