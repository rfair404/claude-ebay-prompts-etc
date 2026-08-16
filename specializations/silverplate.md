# Silverplate flatware & tableware — specialization module

```yaml
triggers: [silverplate, "silver plate", "silver-plate", "silverplated", EPNS, "EP", "quadruple plate", "triple plate", "A1", "AA", "IS", "International Silver", "1847 Rogers", "Rogers Bros", "Wm Rogers", "Wm. Rogers", "Wm A Rogers", "Holmes & Edwards", "Oneida Community", "Community plate", "Tudor Plate", "Reed & Barton" (plate), flatware, silverware, "service for 8", "service for 12", "flatware set", "gravy ladle", "cold meat fork", "sugar shell", "master butter", "hollowware", "tea set" (plate)]
version: 1
last_reviewed: 2026-07-28
coverage: v1 — built for the NOS silverplate flatware batch (mostly flatware + some serving pieces;
  makers seen: 1847 Rogers Bros, Wm Rogers & Son, both International Silver). SOURCE-VERIFIED: the
  Rogers-name disambiguation, mark-reading / dating, the demand-pattern watch-list w/ intro years,
  and the plate-vs-sterling value reality. [DEEPEN] later: Oneida Community & Holmes & Edwards
  pattern rosters, hollowware (tea sets / trays), and live sold-comp bands (PRICE pulls those).
sources:
  - silvercollection.it — 1847 Rogers Bros pattern dictionary (intro years)
  - White Water Antiques — "So what/who is 1847 Rogers Bros" + per-pattern pages (Rogers history, value reality)
  - 925-1000.com / InstAppraisal / antiquesilverhallmarks.com — Rogers & IS mark reading and dating
  - Replacements, Ltd. (replacements.com) — pattern ID reference + replacement (retail) pricing
  - live eBay SOLD comps via Apify (PRICE phase) — the only real number
```

> **Reality check first (the single most useful fact about a box of silverplate flatware).**
> **Silverplate is NOT sterling — it is the opposite pricing play.** Sterling under-prices and we
> push it on melt/rarity (see [`../prompts/price.md`](../prompts/price.md) silver rule). Silverplate
> is a micron of silver over nickel/brass/copper — **melt value is essentially zero** and it is **not
> worth scrapping**. Value comes ONLY from: (1) a **demand pattern**, (2) **completeness** (a real
> service beats loose pieces), (3) **condition** — plate *wears through* to base metal, which kills
> it — and (4) the **NOS premium** (un-used, un-worn, ideally still boxed/sleeved). Most silverplate
> flatware is near-worthless individually; the money is in the right pattern, complete, NOS, bundled
> smart. Do **not** apply the sterling push-high/melt-floor logic here.

## When this applies

Any **plated** (not solid-silver) flatware, serving pieces, or hollowware: forks/spoons/knives sold
as "service for N", serving/oddment pieces (ladles, cold-meat forks, sugar shells, master butter,
pierced servers), and plated hollowware (trays, tea/coffee services, bowls). Triggered by a **plate
mark** (see below) or a known plate maker/line.

**The plate vs. solid-silver fork in the road (decide this FIRST — it changes the whole valuation):**
- **PLATE** (this module) — any of: `SILVERPLATE`, `SILVER PLATE`, `EP`, `EPNS`, `A1`, `AA`,
  `triple/quadruple plate`, `silver on copper`, `IS` next to a Rogers name, or a Rogers/Community/
  Holmes&Edwards line. **No melt floor.**
- **SOLID SILVER** (→ NOT this module; use the [jewelry](jewelry.md) silver-hallmark decode + PRICE's
  silver rules) — `STERLING`, `925`, `800/835/900`, a lion-passant/assay hallmark. Has a hard melt
  floor and is worth 10–100× the plated equivalent. **A "Rogers" name alone does NOT mean sterling —
  almost all Rogers flatware is plate.**
- **Base alloy, no silver at all** — `nickel silver` / `German silver` / `alpaca` / `stainless`. Even
  cheaper than plate; identical valuation logic (design/pattern only).

**Carve-out:** solid **sterling** flatware (Towle, Gorham, Wallace sterling patterns, etc.) is a
separate, much-higher-value game — melt floor + sterling push-high. If the mark says STERLING/925,
leave this module.

## Fast triage (the big-lot pass — for ~600 pieces in ~10 groups)

Goal: sort each group into **DEMAND-PATTERN / COMMON / FILLER** and flag NOS, in ~30 sec/group. The
decisive first action is **read the mark on the back of the handle** (near the base), then eyeball
the handle-front pattern and the wear.

**PULL / look-closer signals:**
- **A demand pattern** off the watch-list below (First Love, Eternally Yours, Daffodil, Heritage,
  Flair, Adoration, Remembrance, Marquise…). These are the few plate patterns that actually move.
- **NOS / boxed** — original wood chest or gift box, anti-tarnish sleeves, unused mirror finish,
  tissue still on pieces. This is your headline pitch; it can 2–3× a common pattern.
- **A complete or near-complete service** (matched forks + spoons + knives for 8 or 12) — a set
  sells; 30 random teaspoons do not.
- **Serving / oddment pieces** — cold-meat fork, gravy ladle, sugar shell, master butter, pierced
  server, berry spoon, cocktail forks. Higher $/piece than teaspoons and useful to lot up.
- **Hollowware** (trays, tea sets) — different category, price separately.

**FILLER signals (the bulk):**
- Common utility line (plain "Wm Rogers & Son AA", tipped/plain patterns), **wear-through to copper/
  brass** at the high points (tines, spoon bowls, handle backs), pitting, bent pieces, monograms
  (hurt on flatware), loose knife handles / rattling blades, orphan singles with no matches.

**When in doubt, pull it** — but for a 600-piece pile the honest expectation is that **most of it is
COMMON/FILLER volume**, with value concentrated in a few demand-pattern groups and the NOS boxed sets.

---

## The "Rogers" name maze (READ THIS — it's the #1 ID trap)

"Rogers" got licensed and imitated to death; the name alone tells you almost nothing. Two corporate
umbrellas own nearly all of it:

**International Silver Co. (Meriden CT)** — owns:
- **1847 Rogers Bros.** ← the flagship; where the *sellable* patterns live. ("1847" is a heritage
  date, NOT the year the piece was made.)
- **Wm. Rogers** / **Wm. Rogers & Son** / **Wm. Rogers Mfg. Co.** — a step down, utility grade.
- **Rogers & Bro.**, **Holmes & Edwards** (its own good patterns: Danish Princess, Spring Garden,
  May Queen).

**Oneida (Oneida Community, NY)** — owns:
- **Community** / **Oneida Community** (good patterns: Coronation, Milady, Adam, Evening Star, South
  Seas, White Orchid), **Tudor Plate**, **Wm. A. Rogers**, **Par Plate**.

**⚠ The trap:** **Wm. Rogers & Son** (International) vs **Wm. A. Rogers** (Oneida) — one letter, two
different companies. Read the mark exactly.

**Reading & dating the mark (on the back of the handle, near the base):**
- **`IS` beside a Rogers name = International Silver → the piece is post-1898** (IS absorbed the
  Rogers brands then). A firm "no earlier than" cue.
- **`AA`, `A1`** = plating-grade marks (better/heavier plate), NOT a date and NOT sterling.
- **`XII` / `12 dwt`** = extra-heavy plate weight designation (better).
- **Star + eagle flanking "Wm. Rogers"** = a *decorative pseudo-hallmark* imitating British/coin-
  silver stamps — **no legal/assay meaning**, do not read it as sterling or as a date.
- **The pattern name is often printed on the box/chest** if NOS — easiest ID of all.
- Cross-check the handle-front design against **Replacements.com** (the pattern-ID bible) or
  silvercollection.it before committing a pattern name.

## The money patterns (the watch-list — 1847 Rogers Bros unless noted)

These are the few plate patterns with real, repeat resale demand (still bought for use/decor and to
complete sets). Intro years from silvercollection.it:

| Pattern | Intro | Look / tell | Note |
|---|---|---|---|
| **First Love** | 1937 | slim Art-Deco handle, delicate raised border + small floral spray | the single **most common** Rogers pattern — always in demand *but* huge supply, so price competitively |
| **Eternally Yours** | 1941 | flowing ribbon + leaf motif, wide sculptural tip | bridal best-seller for 20 yrs; strong demand, big boxed sets |
| **Daffodil** | 1950 | large daffodil blossom at handle top | beloved MCM pattern (disc. 1973) |
| **Remembrance** | 1948 | traditional floral, symmetrical | "ever-popular" traditional |
| **Heritage** | 1953 | ornate scrolled/beaded border | dressy, sought |
| **Flair** | 1956 | slim, single flower spray, mid-century clean | very common but liked |
| **Adoration** | 1930 | (Wm Rogers & Son line) rose/floral | one of the few Wm Rogers & Son patterns with a following |
| **Marquise** | 1933 | geometric Deco | Deco collectors |
| **Reflection** | 1959 | plain modern, tapered | MCM/minimal demand |
| **Springtime** | 1957 | floral spray | MCM |
| **Leilani** | 1961 | tropical/leaf | MCM niche |
| **Magic Rose** | 1963 | rose | niche |

Other IS-umbrella demand patterns to recognize: **Holmes & Edwards** *Danish Princess*, *Spring
Garden*, *May Queen*; **Community** *Coronation*, *Milady*, *Evening Star*, *South Seas*, *White
Orchid*. Everything **not** on a demand list (plain/tipped, generic "Wm Rogers & Son AA" utility
patterns, worn commons) = **volume/filler**, price to move.

## Value drivers (priority order — plate-specific)

1. **Pattern demand.** A watch-list pattern vs. a generic one is the biggest swing. (No melt floor to
   fall back on — unlike sterling, a boring plate pattern can be near-$0.)
2. **Completeness / set integrity.** A matched **service for 8 or 12** >> loose pieces. Odd counts and
   orphans drag. Selling as a *set* is the whole strategy here.
3. **Condition — plate wear.** Un-worn mirror finish vs. wear-through to copper/brass. Plate loss is
   terminal (can't be polished back). This is where NOS wins.
4. **NOS / boxed.** Original chest/box + anti-tarnish sleeves + unused = the premium pitch; can 2–3×
   a used common set. It's the reason this batch is worth listing at all vs. scrapping.
5. **Serving/oddment pieces.** Modest per-piece premium over teaspoons; good for building lot value
   and for standalone listings of the scarcer servers.
6. **No monogram.** Monograms *hurt* flatware resale (opposite of some antique hollowware). Note them.
7. **Demand/comps.** Live SOLD comps decide the number (PRICE) — see the pricing note below.

## Condition grading (silverplate-specific)

- **Plate loss / wear-through** — base metal (copper/brass/nickel) showing at high points: tine tips,
  spoon-bowl backs, knife shoulders, handle backs. Grade by how much and where. **The #1 value cut.**
- **Pitting / black spots** — corrosion through the plate; not polishable out.
- **Knife condition** — plated hollow-handle knives: check for **loose/rattling handles**, cement
  oozing, **stained or pitted stainless blades**, bent blades. Common failure point.
- **Scratches / haze / patina** — surface wear from use; tarnish (polishable) ≠ plate loss (terminal).
- **Bends, dents** (esp. tines, hollowware).
- **Monogram** — disclose; it narrows the buyer pool.
- **NOS claim** — only call NOS if genuinely unused: mirror finish, no scratches in bowls/tines,
  original packaging/sleeves. Don't over-claim (a clean used set is "excellent", not NOS).

## Authentication & fakes

Little outright faking here (low value = low motive). The real errors are **misidentification**:
- **Plate mistaken for sterling** — the big one. "Rogers" ≠ sterling; `IS/EP/A1/AA/silverplate` = plate.
  Only `STERLING`/`925`/assay hallmark = solid. Always read the mark before valuing.
- **Wrong Rogers company** — Wm. Rogers & Son (IS) vs Wm. A. Rogers (Oneida); pseudo-hallmark star+eagle
  read as a real assay mark. Read exactly.
- **"1847" read as a date** — it's a heritage brand date, not year of manufacture.
- **Reproduction/continued patterns** — some patterns ran for decades; intro year ≠ this piece's age.

## Price tiers (coarse signal bands — live SOLD comps decide)

Triage signals, **not quotes**. Note these are much lower than sterling; and beware online **asking**
prices (1stDibs/Etsy/Replacements retail) which run 3–10× real eBay SOLD. PRICE hunts the sold number.

| Tier | What lands here |
|---|---|
| **$ (filler / volume)** | Loose common teaspoons/forks, worn/plate-loss pieces, generic Wm Rogers & Son utility patterns, orphans, monogrammed. Sell by the lot; often $0.50–2/piece equivalent. |
| **$$ (decent set)** | A complete-ish used service for 8/12 in a common-but-liked pattern (First Love, Flair), clean, no boxes. Set sells low-mid **tens of dollars**. |
| **$$$ (demand + condition)** | A demand pattern (Eternally Yours, Daffodil, Heritage), complete service for 12, excellent/near-NOS. Mid **tens to low hundreds** for a big service. |
| **$$$$ (NOS boxed / big complete)** | Original-box NOS demand-pattern chest set, service for 12 with serving pieces, mint. The top of the plate range — **low hundreds** is realistic on a strong pattern; four-figure results exist but are outliers, not the expectation. |

(For calibration from live listings: a common ~82-pc First Love set *asks* ~$200; a service-for-12
Eternally Yours *asks* ~$850 — both **asking**, sold is typically lower. Individual plate serving
pieces sell ~$10–15. Numbers to confirm with SOLD comps, not adopt.)

## eBay listing strategy (sell as sets + drive multi-item orders)

The user's goal — sell as sets, encourage combined orders. Native eBay tools that fit:
1. **List each group as a lot / service.** Title formula: `[Maker] [Pattern] Silverplate Flatware
   Service for [N], [count] pcs [NOS/Excellent]` — e.g. *"1847 Rogers Eternally Yours Silverplate
   Flatware Service for 12, 67 pcs NOS Boxed"*. Sets sell; loose piles languish.
2. **Combined-shipping rule.** Set one shipping-discount profile → buyer's 2nd+ item ships at a
   reduced/flat add-on. Flatware is heavy (metal) so this genuinely moves multi-buys.
3. **Volume/order discount.** "Buy 2 save 10% / Buy 3 save 15%" promotion to push multi-set orders.
4. **Best Offer on everything** (category norm). Ceiling-first pricing posture still applies — list at
   the top supported SOLD comp + Best Offer (per the house pricing memory), not rock-bottom.
5. **Serving pieces — set-in vs peel-out.** Keeping servers in a set boosts the set; the scarcer
   servers (cold-meat fork, pierced server) can also do better listed solo. Decide per group.
6. **Cross-sell the pattern.** Buyers completing a set buy multiples — group same-pattern listings and
   the combined-shipping rule captures the extra pieces.

## Inspection shots (request via needs_followup_photo)

- **Mark macro** on the back of a handle (raking light) — maker + pattern + `IS/AA/A1/STERLING` read.
  The single most valuable shot (confirms plate-vs-sterling and dates it).
- **Handle-front** of one fork + one spoon flat — for pattern ID against Replacements.
- **The whole group laid out** — to count the service (how many of each piece) and spot orphans.
- **High-points close-up** (tine tips, spoon-bowl backs, knife shoulders) — to grade plate wear.
- **Any box/chest** (outside + inside with pieces) — proves NOS + often prints the pattern name.
- **Knife check** — handle-to-blade joint and blade face, for loose handles / blade staining.

## Output hooks (maps onto IDENTIFY fields)

- **Brand** → the exact maker line read from the mark: `1847 Rogers Bros. (International Silver)`,
  `Wm. Rogers & Son (International Silver)`, etc. Never just "Rogers"; never "sterling" unless stamped.
- **Type** → `silverplate flatware — [pattern] — service for N` or `silverplate [serving piece]` /
  `silverplate hollowware — [form]`. Carry the plate claim explicitly (silverplate, not silver).
- **Era** → from the mark: `IS` ⇒ post-1898; pattern intro year as a "no earlier than" (with
  `[ASSUMPTION]`, since patterns ran for decades). Don't read "1847" as the date.
- **Condition** → the plate rubric above (plate wear/loss, knife condition, monogram, NOS claim).
- **Distinguishing marks** → record the **value tier** ($–$$$$), the exact mark text, pattern name +
  intro year, piece count / service size, NOS/boxed status, monogram. This is what PRICE/CURATE triage.
- **needs_followup_photo** → usually the **mark macro + handle-front + group-laid-out count** shot.
- **eBay item specifics (DRAFT must emit)** — silverplate flatware categories commonly require:
  `Brand` (maker), `Pattern`, `Material` = `Silver Plate`, `Type` (e.g. Flatware Set / Serving Piece),
  `Number of Pieces`, `Service for` (N), `Composition`. Missing a required aspect 400s the publish
  (see [`../memory` eBay publish gotchas]). Put pattern + service size in the title.

## Sources

- **silvercollection.it** — 1847 Rogers Bros pattern dictionary with intro years (the year table above).
- **White Water Antiques blog + pattern pages** — the Rogers-name history and the plate-value reality
  (melt value very low vs sterling; only a handful of patterns in resale demand).
- **925-1000.com / InstAppraisal / antiquesilverhallmarks.com** — Rogers & International Silver mark
  reading and post-1898 `IS` dating; the star+eagle pseudo-hallmark.
- **Replacements, Ltd. (replacements.com)** — the pattern-ID reference and replacement (retail) pricing
  — read for ID and relative desirability, **never** as a resale comp (retail runs far above eBay sold).
- **Live eBay SOLD comps via Apify** (PRICE phase) — the only real number; asking prices on 1stDibs/
  Etsy/Replacements are not comps.

> **Open items to deepen (v2):** Oneida Community & Holmes & Edwards full pattern rosters with intro
> years; hollowware (tea/coffee services, trays) valuation; and live SOLD-comp bands per demand
> pattern (First Love, Eternally Yours, Daffodil, Heritage) once PRICE has run this batch.
