# Jewelry — specialization module

```yaml
triggers: [jewelry, jewellery, ring, necklace, pendant, locket, bracelet, bangle, brooch, "brooch pin", earrings, cufflinks, "tie clip", chain, charm, parure, hallmark, karat, "10k", "14k", "18k", "carat gold", sterling, "925", "gold filled", "gold-filled", vermeil, "costume jewelry", rhinestone, "signed jewelry", "diamond ring", "gemstone ring", "set gemstone"]
version: 1
last_reviewed: 2026-06-18
coverage: v1 (built from four deep-research passes). SOURCE-VERIFIED: metals/marks (FTC);
  British + German + Russian + Mexican hallmark decode; diamond + corundum + assembled-stone +
  jade + ivory + glass-paste + pearl tells; and the maker roster Tiffany / Cartier (mark only,
  not serial) / Mikimoto / Boucher / Juliana / Trifari / Coro-Vendome-Duette / Eisenberg /
  Schreiner / Miriam Haskell / Hobé. STILL [DEEPEN] (routed to reference library, held [BEST-CASE]):
  French + Italian + Asian hallmark decode; the remaining fine makers (VCA, Bulgari, Georg Jensen,
  Boucheron, Webb, Yurman, Hardy, Buccellati, Harry Winston); turquoise/amber/coral/tortoiseshell/
  emerald organics; the Cartier serial-number format (claims were refuted — do not state).
sources:
  - US FTC, 16 CFR Part 23 "Guides for the Jewelry, Precious Metals, and Pewter Industries" (2018 rev) — primary law for metal/karat/plating terms
  - GIA — gia.edu (gem imitation/simulant definitions; jade FTIR primary; diamond 4Cs; the 7 Pearl Value Factors)
  - International Gem Society / Gem Society + Gem-A — gemsociety.org, gem-a.com (synthetics, assembled stones, jade, glass paste)
  - Lang Antiques / Antique Jewelry University — langantiques.com/university (gold terminology, maker-mark database, gem references, period timeline)
  - The Goldsmiths' Company Assay Office London + The Silver Society — UK hallmark structure & date letters
  - CITES — Identification Guide for Ivory and Ivory Substitutes (Schreger lines; legal status)
  - The Online Encyclopedia of Silver Marks (925-1000.com) + silvercollection.it — silver/maker/foreign assay marks (German, Russian, Mexican decode)
  - costumejewelrycollectors.com (CJCI) + morninggloryjewelry.com — costume maker marks & dating rosters
  - Mikimoto America brand FAQ + GIA — pearl authentication & grading
  - Spratling Silver (spratlingsilver.com), Jewelers Mutual, AC Silver — corroborating references
```

> **Reality check first (the single most useful fact for an estate box of jewelry).** Most
> of a typical estate jewelry lot is **unmarked or base-metal COSTUME** with little melt or
> maker value. The money sits in the few pieces that carry (a) a **precious-metal mark**
> (10k/14k/18k, sterling/925, plat), (b) a **signed maker** (fine OR collectible costume), or
> (c) a **genuine gemstone**. The job is to *find and read every mark first*, pull those few,
> and not over-grade the rest. Fine jewelry has a **hard melt/scrap floor** that costume does
> not — that floor is the safety net under every precious-metal pull.

> **Match marks and stones against the reference library, not memory.** Before committing a
> maker, a hallmark's origin/date, or a "genuine gemstone" claim that moves value, **WebFetch
> the matching reference page (below) and compare the actual mark/stone photo.** Maker punches,
> assay marks, and gem look-alikes are exactly where memory misleads (a counterfeit "925"/
> "TIFFANY & CO." stamp, a doublet that looks solid face-up, a Verneuil synthetic that looks
> natural). The honesty rules in [`../prompts/_shared.md`](../prompts/_shared.md) and the
> **maker-mark gate** in [`../prompts/identify.md`](../prompts/identify.md) apply in full — a
> stamp *indicates* but does not *prove*; many decisive tests need a bench jeweler or gem lab.

## When this applies

Any wearable jewelry — ring, necklace/pendant/locket, bracelet/bangle, brooch/pin, earrings,
cufflinks, chain, charm, parure — whether **fine** (precious metal / gemstone / signed designer)
or **costume** (base metal, rhinestone, Bakelite, enamel, signed costume makers). Includes loose
gemstones set in jewelry and unset stones offered with a piece.

**Maker-mark GATE (this is a gate category).** Jewelry, precious metals, and set glass/gemstones
are all on the [`identify.md`](../prompts/identify.md) maker-mark stop-and-ask list. When a mark
(karat stamp, hallmark, signature) is plausibly present but not decisively readable from the
photos, **STOP and ask the user to read the inside/underside/clasp marking** before spending on
research or settling Brand/metal — their close-read of the stamp beats any guess. Headless: degrade
to `needs_followup_photo` + a `NEEDS_REVIEW.md` line and proceed.

**Carve-outs / NOT this module:**
- **Watches / wristwatches / pocket watches** — deep separate category; a future `watches.md`
  module. (A gem-set watch's *stones/metal* can still use this module's metal & stone tells.)
- **Holloware / flatware / tableware silver** — it's silver, and this module's silver-hallmark
  decode applies, but those are tableware, not jewelry. (PRICE's silver push-high + melt-floor
  rules still govern pricing — see [`../prompts/price.md`](../prompts/price.md).)
- **Loose mineral specimens / lapidary rough** that are not gem-quality or set — minerals, not jewelry.
- **"Agate marble" / glass-ball toy** → the [marbles](marbles.md) module, not this one (a loose
  agate *gemstone* or an agate *cabochon set in jewelry* IS this module).

## Fast triage (the estate-box pass)

Sort the lot into **PULL** (look closer / read the mark) vs **FILLER** (bulk costume) fast. The
decisive first action is always **find and read every mark** (base, inner band, clasp, pin stem,
back of a brooch, earring post).

**PULL signals (worth the close look):**
- **Any precious-metal mark** — gold karat (`10K 14K 18K 22K 24K`, or `+P`/`KP`), fineness number
  (`375 417 585 750 916 999`), `STERLING`/`925`/`800`/`835`/`900`, platinum (`PLAT 950 PT950 900`).
- **Any signed maker** — fine (TIFFANY, CARTIER, etc.) OR collectible costume (Trifari crown,
  EISENBERG, Boucher MB-cap, HASKELL, unsigned-but-Juliana construction).
- **Genuine-looking gemstones** — real diamonds, ruby/sapphire/emerald, natural pearls, jadeite,
  fine opal. (Verify; see Stones below — most "gems" in costume are glass.)
- **Old construction** — C-clasp / trombone clasp brooch, cut-down/closed-back/foil-backed settings,
  screw-back earrings, hand-fabrication → likely pre-WWII (see Period dating).
- **Bakelite** and other early plastics (hot-water/Simichrome tests — in-hand only).
- **Matched parure/demi-parure** (necklace + bracelet + earrings + brooch en suite).
- Anything heavy-for-its-size in a precious-metal color. **When in doubt, pull it.**

**FILLER signals (the bulk):**
- Unmarked base metal, no signed maker, missing/replaced stones, heavy plating loss, broken
  findings, modern mass-market costume; bag-fresh identical repeats.
- A `925`/karat stamp on a piece that is magnetic or shows base-metal wear-through → **suspect a
  faked stamp** (a stamp indicates, does not prove — flag for acid/density test).

---

## Metals & their marks (SOURCE-VERIFIED — FTC 16 CFR Part 23)

The mark tells you the metal *claim*; only an in-hand acid/XRF/density test *proves* it. A
**photo seller cannot confirm fineness** — read the stamp, then flag verification.

**Gold — karat ↔ fineness (parts-per-24 ↔ parts-per-1000):**

| Karat | Stamp | Gold % | Fineness mark |
|---|---|---|---|
| 9K | 9K / 375 | 37.5% | 375 |
| 10K | 10K / 417 | 41.7% | 417 |
| 14K | 14K / 585 | 58.3% | 585 |
| 18K | 18K / 750 | 75.0% | 750 |
| 22K | 22K / 916 | 91.7% | 916 |
| 24K | 24K / 999 | 99.9% | 999 |

- US/Canada stamp **karat** (number + `K`/`KT`); Europe/Japan/Mideast stamp **millesimal fineness**
  (the 3-digit number). Either is "solid gold" of that purity.
- **`P` / `KP` = plumb** — the alloy tests to *exactly* the marked fineness (tight tolerance), e.g.
  `14KP` = exactly 58.3%. Convention from the 1976 US Stamping Act amendment (effective 1981).
- **Not solid gold — the regulated layered tiers (mark says so):**
  - **Gold-filled (`GF`)** — karat-gold layer **≥ 1/20 (5%) of total weight**, gold ≥10K. Stamp
    encodes fraction+karat: `1/20 12kt GF`, `1/20 14K GF`, heavier `1/10 12K GF`. (FTC 23.3)
  - **Vermeil** — **sterling-silver base** + gold ≥10K, **≥2.5 micron** thick. (FTC 23.4) The
    silver base is what separates vermeil from gold-filled.
  - **Rolled gold plate (`RGP`)** — applied gold ≥10K, layer *below* the 1/20 GF threshold (down to
    the 1/40 floor); states karat when marked.
  - **Gold electroplate (`GP`/`GE`/`GEP`)** — ≥0.175 micron of ≥10K. **Heavy gold electroplate
    (`HGE`)** — ≥2.5 micron. ⚠ HGE marks are **unregulated in practice** and often mean only thin
    base-metal plating — treat HGE as costume, not gold.
  - `1/20`, `RGP`, `GF`, `GP`, `HGE`, `gold tone` → **NOT solid gold**; melt-floor logic does not apply.
- ⛔ Do **not** repeat these refuted myths: GF is "5–10× thicker than plating" (false heuristic);
  "HGE has no legal definition" (FTC does define it); "10K is the US legal minimum and below-10K is
  misbranded" (overstatement) — all three were specifically refuted against FTC primary text.

**Silver:**
- **Sterling = 92.5% silver** → marked `STERLING`, `925`, or `.925`. Coin = `900`; continental
  `800`/`835`/`900`; fine `999`. The stamp **indicates**, does not **prove** — a faked `925` on
  plated brass/nickel is common; verify with magnet (silver is non-magnetic) + acid/density in hand.
- **NOT silver, despite the word:** `EPNS` / `EP` / "quadruple plate" / "silver on copper" =
  silver-PLATE over base; **"nickel silver" / "German silver" / "alpaca"** = a copper-nickel-zinc
  BASE alloy with **no silver content at all**. (PRICE still treats the *word* "silver" as a
  push-high trigger regardless — see its silver rule — but for ID these are base metal.)

**Platinum / palladium:** `PLAT`, `PT`, `950`, `PT950`, `900`, `IRIDPLAT`, `PLAT900`. Dense, white,
non-tarnishing. **White gold vs platinum vs silver** is not photo-decidable beyond the stamp —
white gold is marked with a karat; platinum with `PLAT/950`; flag for weight/test.

**Base metals:** brass, "pot metal" (low-melt zinc alloy, common in vintage costume), nickel/German
silver, stainless, pewter. No melt floor; value is maker/design only.

**Photo-only limits (state honestly):** you can read the *stamp* from a macro; you **cannot** confirm
metal content, detect a faked stamp, or weigh for melt from a photo. Acid test, XRF, and scale are
**in-hand** steps → request them via `needs_followup_photo` / the maker-mark gate.

## Hallmarks & makers' marks by country

**United States** — no national assay office. A US piece carries a **fineness/karat mark + a
registered maker's trademark** (and often `STERLING`). The **word `STERLING`** skews older/US;
the **numeral `925`** skews later or import — a soft era/origin cue, not proof.

**British (assay-office) — the decodable system (VERIFIED):** a UK hallmark is **4–5 component
marks**. Modern (post-**1999**) the **three compulsory** marks are: **sponsor's (maker's) mark** +
**millesimal fineness number** (e.g. 925, 750) + **assay-office mark**. The traditional **fineness
symbol** and the **date letter** are now *optional* (London still applies the lion as standard).
- **Lion passant** = English **sterling** standard (used by all English offices after **1720**);
  higher **Britannia** standard shows a seated Britannia figure instead.
- **Assay-office/town marks:** London = leopard's head · **Birmingham = anchor** · Sheffield = crown
  (rose post-1975) · Chester = sword between three wheatsheaves · Edinburgh = three-towered castle.
- **Date letter** = a letter whose **font AND enclosing-shield shape together** give the year (each
  cycle spans defined years) → decode **only** with a date-letter chart for that office (reference lib).

**German (VERIFIED).** The unified national mark is a **crescent moon + imperial crown**
(*Halbmond und Reichskrone*), set by the law of 16 July 1884 (effective 1888), minimum national
fineness **800/1000**. Crescent+crown ⇒ German origin + ≥800 silver; the accompanying number
(`800`/`835`/`900`/`925`) gives exact fineness.

**Russian (VERIFIED) — the kokoshnik (woman's-head) assay mark + zolotnik fineness.** Official from
the 1896 reform (in use ~1899). **Head direction is a photo-checkable dating cue:** head facing
**LEFT** = **1899-1908**; a more detailed head facing **RIGHT** = **1908-1926** (the later mark adds
a Greek-letter office code — α St. Petersburg, δ Moscow). Fineness is **zolotnik** (`84` = .875,
`88` = .916, `91` = .947; the older `56/72/96` for gold). ⚠ The right-facing kokoshnik was
**reinstated in 1985**, so right-facing alone is not conclusively pre-1926 — corroborate with maker/
zolotnik context. Hold the date at `[BEST-CASE]`.

**Mexican silver (VERIFIED).** Two systems:
- **"Eagle" assay marks** (1948-~1979): a fully-drawn (delineated) eagle ran to ~1955, a silhouette
  eagle to the early 1970s. The **number on the eagle's chest = city OR maker** (#1 = Mexico City,
  #3 = Taxco, #16 = Margot de Taxco); pieces also carry `925`, `TAXCO`, maker initials.
- **Post-~1979 letter/number registration** (guarantees sterling): **1st letter = city** (T Taxco,
  M Mexico City, G Guadalajara, C Cuernavaca) · **2nd letter = silversmith's surname initial** ·
  **number = that smith's registration order**. e.g. `TC-45` = Taxco / surname-C / #45, post-1979.
  Higher number = newer; there is **no published name list**, so it rarely resolves to a person.
- **Spratling (Taxco)** mark periods: `WS Print` ≈1931-46 · `Spratling de Mexico` ≈1949-51 ·
  `WS Script` variants ≈1951-67.

**French / Italian / Scandinavian (Georg Jensen) / Asian (Chinese export, Japanese, Thai/Siam)** —
**[DEEPEN — not yet source-verified here].** Decode via the reference library, hold origin/date at
`[BEST-CASE]`. Anchors to verify there: **French** eagle-head (18k gold), Minerva (silver), owl
(import); **Italian** fascione (star + province number + fineness, post-1968); **Georg Jensen**
post-1945 oval + design numbers; **Chinese export** pseudo-English hallmarks + character chops;
**Japanese** `950`/`JUNGIN`, "Occupied Japan" (1945-52).

**Country-of-origin wording dates a piece.** The McKinley Tariff (1891) required country marking on
imports → bare "England"/"France" vs "Made in …" vs "Occupied Japan" (1945-52) are era cues (verify
in the library before relying on them).

## Signed makers (the names that move price)

Brand is the highest-leverage field here. A confirmed maker can multiply value over scrap. Read the
signature, then **match it to the reference library** — do not promote a guess.

**FINE — VERIFIED mark structure:**
- **Tiffany & Co.** — authentic reads `TIFFANY`, `TIFFANY & CO.`, or `T&Co.`, sometimes in a
  cartouche/circle/oval, with `925`/`STERLING` or a karat mark. Genuine-vs-fake turns on punch
  alignment/centering/typography — an in-hand authentication check, not the word alone.
- **Cartier** — the signature appears in many cartouche/frame shapes across Paris/London/New York
  and ~175 years; genuine pieces carry a **serial number** (its presence is an authentication
  check). ⚠ The exact serial/reference-number **format is NOT reliably documented** (the
  commonly-cited online formats failed verification) — do **not** date or authenticate from the
  serial structure; treat a magnified punch + professional auth as the path.
- **Mikimoto (pearls) — VERIFIED.** The trademark is an **Akoya-oyster/clamshell outline enclosing
  an `M`** (or the engraved Mikimoto name) + a fineness mark (`18K`/`S`/`SL`/`Sterling`/`950`). On a
  strand/bracelet it is **on the BACK OF THE CLASP**, and the strand also carries a signature `M`
  charm. **Because the clasp is the only marked spot, a strand/bracelet CANNOT be authenticated as
  Mikimoto without its original clasp** (very early/wartime strands may be unmarked or carry only a
  plain stamped `M`). The pearls themselves are graded, not branded — see Stones.
- **Others (VCA, Bulgari, Boucheron, Georg Jensen post-1945 oval + design numbers, Buccellati,
  David Webb, David Yurman, John Hardy, Harry Winston)** — **[DEEPEN]**: real signed pieces exist
  and move value hard, but their exact mark/serial structures are **not yet verified here** → settle
  Brand only by matching the AJU maker-mark database (reference lib); else `[BEST-CASE]`.

**COSTUME — VERIFIED dating cues:**
- **Marcel Boucher** — `MB + phrygian cap` ≈ **1937-1949**; plain `Boucher` ≈ 1950-1955; **`©Boucher`
  ≈ 1955 onward** (US designs couldn't be copyrighted until 1955). Inventory numbers rough-date
  (~2300s c.1945 → 8291 by 1962), later add `P`/`E`/`N` (pin/earring/necklace). The © is firmer than
  the number. ⚠ The **`©` rule is Boucher-specific / directional — NOT a universal "©=post-1955"
  law** (that generalization was refuted; some makers differ).
- **DeLizza & Elster "Juliana"** — essentially **UNSIGNED** ("Juliana" is a collector nickname;
  only brief paper hang-tags c.1967). Attribute by the **construction battery**, no single trait
  conclusive: 5-link bracelets, open-back navette/foiled settings, solder "puddling," japanned
  (blackened) metal, **sparing rivets** (heavy rivet counts → Beaujewels/Judy Lee, *not* Juliana).
  Authentic bracelets **usually prong-set** the larger stones → **all-glued stones = reproduction
  red flag** (recent Asian imports glue everything) — EXCEPT genuine *cast* D&E designs are legitimately
  all-glued (faux prongs).
- **Trifari (VERIFIED).** `TKF`/`KTF` ≈1935 (KTF on jewelry 1935-37) → switched to the **`Trifari`**
  mark **Dec 1937** (KTF/Trifari overlap ~1937-39, a gradual transition). `Clip-mates` inside a
  twin-clip mechanism = 1936-37 (KTF) or Dec 1937+ (with `Trifari`). The **Crown-Trifari mark with
  `©` dates after 1955** (Crown+© used 1955-1969) — a **Trifari-specific** copyright cue, not a
  universal rule.
- **Coro / Vendome / Duette (VERIFIED).** **Vendome** = Coro's upscale line, c.1944-1979 (`Vendome ©`
  ⇒ after 1955). The **Coro Duette** (two clips on a removable frame) uses a locking mechanism
  introduced **1935** (Candas patent Coro bought 1933) — a Duette frame is a ~1935+ cue.
- **Eisenberg (VERIFIED — use the CJCI timeline).** `Eisenberg Original` ≈1935-1945; **sterling**
  marks 1943-early 1948 (dropped late 1948); **`Eisenberg Ice` in block letters WITH `©` = 1970-
  present**. (Distinguish the marked-piece era from the 1942 trademark *filing*.)
- **Schreiner of New York (VERIFIED).** `SCHREINER OF NEW YORK` ≈1939-1977. Unsigned tells:
  **inverted/keystone stone settings, hook-and-eye construction**.
- **Miriam Haskell (VERIFIED).** **Unsigned 1926-~1947** → impressed **round** mark 1947-49 →
  **horseshoe** plaque (earliest signature) mainly 1948-50 → **oval** plaque/hang-tag soldered to
  filigree, **stamping showing through to the back**, ~1950-51 to today.
- **Hobé (VERIFIED).** **Triangle** mark 1933-1957; `Sterling` 1941-47; **`©` 1958-1983** (a
  Hobé-specific window, not the universal © rule). Trademark first used 1926.
- **Others (Corocraft, Weiss, Schiaparelli, Sherman/Canada, Napier, Monet, Sarah Coventry)** —
  **[DEEPEN]**: collectible and datable by mark, but **not yet verified here** (a Schiaparelli
  1931-1973 dating claim specifically **failed** verification — don't state it) → match
  costumejewelrycollectors.com / morninggloryjewelry / illusionjewels (reference lib) before
  asserting; else `[BEST-CASE]`.

## Stones & gems (photo-tells + the honest limits)

The recurring truth: **a few tells are photo-checkable, but most identification of a genuine vs
synthetic/treated/imitation stone needs a loupe, immersion, or a gem lab.** Never state "genuine
ruby/diamond/jade" from a photo alone — say "appears to be / sold as" and flag the lab step.

**Diamond vs simulant (VERIFIED, GIA):**
- **Moissanite** is **doubly refractive** → a **doubled back-facet image** under 10× (blurry interior);
  diamond shows no doubling. In ≥1 ct stones, extreme "disco-ball" fire is a naked-eye non-diamond flag.
- **CZ** — Mohs 8.5 (vs diamond 10), usually colorless, **yellows over time**, **more fire / less
  brilliance** than diamond. Most common diamond simulant.
- A `simulant` (CZ, moissanite, white sapphire, glass/paste) looks like the gem but differs in
  chemistry/structure; a `synthetic` shares the natural's chemistry. Diamond authenticity beyond
  these tells (and lab-grown-vs-natural) needs a tester/lab.

**Corundum (ruby/sapphire) & glass (VERIFIED, IGS/Lotus):**
- **Curved growth lines (striae)** = **synthetic** (Verneuil flame-fusion); no natural mineral shows
  curved striae. ONE-WAY: curved striae present → synthetic; *absence proves nothing*. Usually needs
  microscope/immersion → rarely photo-resolvable.
- **Perfectly round / "tadpole" gas bubbles** = synthetic or **glass** (natural cavities are angular).

**Assembled stones — doublets/triplets (VERIFIED, IGS):**
- Solid opal looks uniform from the side; a **doublet** shows a **sharp straight-line join** between
  the opal and a dark base; a **triplet** has a **colorless crown cap** visible from almost any angle
  (easiest to spot). A **garnet-glass doublet** can show no red face-up and no eye-visible join.
- **Bubbles trapped in the glue layer** are the strongest assembled-stone tell — but need
  magnification/immersion. **Closed bezel settings hide the side join** → the side-view tell only
  works on unset / open-back / open-side mounts.

**Paste / rhinestone / glass (VERIFIED tells, Gem-A/IGS):** foil-backed closed-back "paste" (old)
and modern rhinestone are **glass**. Photo/loupe tells: **poor lustre** relative to a real gem,
internal **gas bubbles**, **swirls** (curving internal flow lines), **mould/seam marks**, soft-
rounded facet edges, and "too-perfect" uniform color. Opaque paste almost always shows small
chips/scratches with a **glassy lustre** (polycrystalline gem fractures don't). ⚠ A bright foil
backing inflates apparent brightness — judge lustre with care. Treat any unverified set "gem" in
costume as **glass until proven otherwise**.

**Pearl (VERIFIED, GIA):** graded on the **GIA 7 Pearl Value Factors** — size, shape, color,
**luster**, surface, **nacre** (thickness), matching. Photo cues to *quality*, not authenticity:
high luster (sharp reflections), clean surface, good match across a strand. **What a photo CANNOT
settle:** natural vs **cultured** vs **imitation**, and nacre thickness — these need a lab (GIA
X-ray for natural/cultured + nacre). In-hand only: the gritty "tooth test" (real = slightly gritty,
imitation = smooth) and drill-hole/overtone tells. Brand (e.g. Mikimoto) comes only from the clasp
mark, never the pearl. Flag "appears cultured/imitation, lab-verify" rather than asserting natural.

**Organics & the legal flag:**
- **Jade** — A (untreated; wax ok) / **B (bleach + polymer)** / C (dyed). **Conclusive B-jade ID
  needs LAB FTIR — not photo-confirmable; escalate.** Jadeite-A is the valuable one.
- **Ivory / tortoiseshell — LEGALLY RESTRICTED (CITES + US state law).** Elephant/mammoth ivory shows
  **Schreger lines** (cross-hatch) on a *polished transverse section* (outer angle <90° mammoth,
  >115° elephant, 90-115° overlap — average several) — a measurement test, not a casual photo tell.
  **Flag for compliance before listing.** ⛔ Do **not** use the "real ivory fluoresces under UV /
  plastic stays dull" test — it was refuted against CITES.
- **[DEEPEN]** turquoise (stabilized/block/howlite-dyed), amber (copal/pressed/phenolic — UV/float/
  hot-needle), coral, emerald (jardin/oiling/doublet), garnet/amethyst/topaz/aquamarine/citrine —
  not yet verified here; use the reference library and hold genuineness at `[BEST-CASE]` + a
  lab-verify note. (A "flawless" emerald is almost always glass/synthetic — natural emeralds carry
  jardin inclusions — but confirm before relying on it.)

## Styles & periods (date by construction — [DEEPEN], hold at [BEST-CASE])

Construction/findings often set a **"no earlier than" bound**, not an exact date. Pending a verified
deepen pass, use these as *directional* cues and confirm in the reference library (AJU timeline,
AC Silver clasp guide):
- **Brooch pin mechanism:** C-clasp (no safety) → generally older (pre-~1910); trombone/tube clasp
  → late-19thC–1910s (often European); modern safety/roll-over catch → ~1920s+.
- **Earring findings:** screw-back ≈1900-1950s; clip-back from ~1930s; modern posts (pierced) common
  ~1960s+.
- **Settings:** cut-down / collet / closed-back / **foil-backed** → older (foiled closed backs largely
  pre-~1900); open-back prong/claw settings later.
- Period bands to assign once construction + style agree: Georgian → Victorian (early/grand/late;
  mourning/jet) → Art Nouveau → Edwardian → Art Deco → Retro (1940s) → Mid-Century / Modernist → later.
  Assign with `[ASSUMPTION]` when inferred from style alone.

## Value drivers (priority order)

1. **Metal melt/scrap floor (fine only).** Precious metal sets a hard floor costume lacks. Rough melt
   = (gold karat % OR silver fineness) × **weight** × **spot price**. Needs the *weight* (a photo
   can't give it) → request it. Plated/GF/vermeil/HGE have **no** melt floor.
2. **Signed maker.** A confirmed fine OR collectible-costume maker multiplies value over scrap/base.
3. **Gemstones.** Genuine, sizeable, well-cut natural stones (esp. diamond, ruby, sapphire, emerald,
   natural pearl, jadeite-A) — but only when authenticity is supported; assume glass in costume.
4. **Period / age & design.** Datable antique/period construction + desirable style.
5. **Condition.** See below — repairs and missing stones cut hard.
6. **Demand / brand heat.** Live comps decide (PRICE). No fixed price tiers — tiers below are signals.

## Condition grading (jewelry-specific)

Grade and disclose; jewelry damage is specific and value-moving:
- **Missing / replaced / chipped stones** (note empty seats; replaced ≠ original).
- **Repairs** — resizing marks, solder blobs at shanks/joints, replaced clasp/findings, re-tipped prongs.
- **Metal wear** — thin shanks, worn-through plating (costume), dents, porosity, casting pits.
- **Surface** — scratches, polish loss, patina (desirable on some antiques, not on others).
- **Function** — clasp/catch works; pin stem straight; hinge tight; safety catch present.
- State what **cannot be assessed** from photos: metal content, stone authenticity, exact weight,
  inside-shank marks not shown.

## Authentication & fakes (name the test; flag the lab/bench step)

- **Counterfeit signed marks** (fake `TIFFANY & CO.`/`Cartier`) — check punch alignment/typography
  against the reference guide; serial-format check (Cartier). High-value calls → professional auth.
- **Faked precious-metal stamps** — a `925`/`14K` on magnetic or base-metal-worn pieces → magnet
  (in hand) + acid/XRF; never trust the stamp alone.
- **Glass sold as gem / doublets-triplets** — side-view join + glue-bubble tells (mag/immersion);
  closed bezels hide them → escalate.
- **Undisclosed lab-grown diamond** — needs a tester/lab; cannot be ruled out from a photo.
- **B-jade (polymer)** — needs FTIR; escalate.
- **"Married"/repro costume** (e.g. all-glued "Juliana") — construction battery; one trait is not proof.
- ⛔ **Refuted tests — do NOT use:** UV "real-ivory-glows" test; the universal "©=post-1955 across all
  makers" rule; the over-specified single-feature Juliana description. (Each failed verification.)

Most decisive tests here are **in-hand or lab**. A photo seller's honest output is "appears to be /
sold as, pending verification" + the specific test that would confirm.

## Price tiers (coarse signal bands — live comps decide)

Triage signals, **not quotes**; PRICE hunts real comps and applies the silver/precious-metal push-high
and melt-floor rules. No hard price claims survived verification — treat these as *which bucket to comp*:

| Tier | What lands here |
|---|---|
| **Scrap/melt floor** | Any solid precious-metal piece — never sells below its metal melt (compute from karat/fineness × weight × spot). The floor under every fine pull. |
| **$ (filler)** | Unmarked/base-metal costume, plated (GF/HGE/RGP) with no maker, damaged/missing-stone costume. Sell by the lot. |
| **$$ (better costume)** | Signed collectible costume in good condition (Trifari, Coro, Boucher, Eisenberg, Juliana-attributed, Bakelite). |
| **$$$ (mid fine)** | Solid gold/silver or genuine-gemstone pieces above melt; quality period pieces; lesser-signed fine. |
| **$$$$ (high / designer)** | Signed fine makers (Tiffany, Cartier, VCA, Jensen…), important gemstones, fine antique/period jewelry. |
| **🏆 trophy** | Major signed designer + important natural stones; museum-grade antique; provenance pieces. |

## Inspection shots (request the SPECIFIC macro, via needs_followup_photo)

- **Mark/hallmark macro, raking light** — the karat/fineness/maker stamp on inner band, clasp, pin
  stem, or back. The single most valuable shot (and the maker-mark gate's ask).
- **Weight on a scale (grams)** — required for any melt-floor estimate on precious metal.
- **Stone setting, side/open-back view** — to check for a doublet/triplet join and the setting type.
- **Clasp / findings close-up** — for period dating (C-clasp vs trombone vs modern; earring back type).
- **Any signature / serial** close-up — for signed-maker authentication.
- **Overall + worn areas** — to grade plating loss, repairs, missing stones.

## Output hooks (maps onto IDENTIFY fields)

- **Brand** → the signed maker when the signature is read+matched (Tiffany, Cartier, Boucher…);
  `[BEST-CASE]` + bracket for an unconfirmed/`[DEEPEN]` maker; genuinely unmarked → `Unbranded`
  (last resort, after the maker-mark pass).
- **Type** → the form + metal/stone (e.g. "14K gold sapphire ring", "sterling brooch", "Boucher
  enamel pin", "rhinestone parure"). Carry the **metal claim** exactly as the stamp reads.
- **Era** → from construction/marks, `[ASSUMPTION]` when style-only; never assert antique/origin on a
  single cue. Country-wording cues held at `[BEST-CASE]`.
- **Condition** → the jewelry rubric above (missing stones, repairs, plating loss, clasp).
- **Distinguishing marks** → record the **value tier** ($ / $$ / $$$ / $$$$ / 🏆), the **exact stamp
  text read** (and what it proves vs needs testing), weight if known, stone read + its limit, and
  the period/clasp cue. This is what PRICE/CURATE triage on.
- **needs_followup_photo** → the specific inspection shot above (usually the mark macro + a gram weight).
- **eBay REQUIRED item specifics (INVESTIGATE must list, DRAFT must emit).** eBay's jewelry
  categories enforce required aspects — a missing one **rejects the publish** (verified: a ring
  publish 400'd with errorId 25002 "The item specific Metal is missing"). So for jewelry, INVESTIGATE
  always lists, and DRAFT always emits, these as item specifics (they are eBay-required, not optional
  even when unbranded):
  - **`Metal`** — the metal TYPE: `Yellow Gold` / `White Gold` / `Rose Gold` / **`Two-Tone Gold`** /
    `Sterling Silver` / `Platinum` / `Gold Filled` / `Base Metal` (costume). This is **separate from
    Metal Purity** and is the one most often forgotten.
  - **`Metal Purity`** — `10k` / `14k` / `18k` / `925` / `Platinum`, exactly as the stamp reads.
  - **`Main Stone`** (when set) — e.g. `Diamond` (with the untested/"appears" caveat in the
    description, never asserted as tested from a photo).
  - **Ring** also requires **`Ring Size`**; earrings/necklaces have their own (e.g. fastening,
    length). Put any beyond the template's standard fields in `item_specifics.extra`.

## Reference library (fetch real marks/stones before committing)

Compare the item's photo to the actual reference image/data — never settle a value-moving maker,
hallmark origin/date, or "genuine gemstone" from memory.

**Metals, hallmarks & maker marks:**

| Use it for | URL |
|---|---|
| US metal/karat/plating LAW (primary) | https://www.law.cornell.edu/cfr/text/16/23.3 , /23.4 |
| Gold terminology — karat/plumb/GF/RGP/vermeil | https://www.langantiques.com/university/gold-terminology/ |
| Silver / maker / **foreign assay** marks (the standard ref) | https://www.925-1000.com/ |
| UK hallmark structure (compulsory marks, date letters) | https://www.assayofficelondon.co.uk/hallmarking/uk-hallmarks |
| British silver marks / date-letter decoding | https://www.thesilversociety.org/research/identify-your-silver/ |
| Maker-mark **database** (per-maker pages) | https://www.langantiques.com/university/makers-marks-2/ |
| Tiffany mark variants | https://www.langantiques.com/university/mark/tiffany-and-co/ |
| Cartier mark variants + serial check | https://www.langantiques.com/university/mark/cartier/ |
| Mexican eagle + post-1979 letter/number decode | https://www.925-1000.com/mexican_marks.html |
| Russian kokoshnik head-direction dating | https://www.silvercollection.it/dictionarykokoshnik.html |
| Spratling / Mexican mark periods | https://www.spratlingsilver.com/hallmarks.htm |
| Costume maker marks (Boucher, Juliana, A–Z by signature) | https://www.costumejewelrycollectors.com/vintage-costume-jewelry-research/costume-jewelry-marks/ |
| Costume maker dating roster (Trifari, Coro, Eisenberg, Schreiner, Haskell, Hobé…) | https://www.morninggloryjewelry.com/articles/jewelry-marks-and-dates/ |
| DeLizza & Elster "Juliana" construction tells | https://www.costumejewelrycollectors.com/vintage-costume-jewelry-research/designers-manufaturers-and-styles/identifying-delizza-elster-juliana-jewelry-101/ |

**Stones, gems & dating:**

| Use it for | URL |
|---|---|
| GIA — gem imitation / simulant definitions | https://www.gia.edu/gem-imitation |
| GIA — diamond simulants (CZ, moissanite, lab-grown) | https://4cs.gia.edu/en-us/simulants-moissanite-and-lab-grown-diamonds/ |
| IGS — synthetics/treatments/imitations (curved striae, bubbles) | https://www.gemsociety.org/article/understanding-gem-synthetics-treatments-imitations-part-4-synthetic-gemstone-guide/ |
| IGS — assembled stones (doublets/triplets) | https://www.gemsociety.org/article/assembled-stones-jewelry-and-gemstone-information/ |
| IGS — jade treatments (A/B/C) | https://www.gemsociety.org/article/identifying-jade-treatments/ |
| GIA — bleached & polymer jadeite (FTIR; primary) | https://www.gia.edu/doc/Identification-of-Bleached-and-Polymer-Impregnated-Jadeite.pdf |
| GIA — the 7 Pearl Value Factors (grading) | https://4cs.gia.edu/en-us/blog/pearl-quality-101-gia-examines-classifies-pearls/ |
| Gem-A — glass/paste tells (bubbles, swirls, mould marks) | https://gem-a.com/gem-hub/gem-knowledge-what-is-artificial-glass-paste-in-antique-jewellery/ |
| Mikimoto — pearl authentication (oyster-M clasp mark) | https://www.mikimotoamerica.com/us_en/frequently-asked-questions |
| Verneuil synthetic corundum tells (mirror; the lotusgemology URL 404s) | https://www.ruby-sapphire.com/ |
| Old paste / closed-back glass | https://www.langantiques.com/university/paste-2/ |
| CITES — ivory ID & Schreger lines + legal status | https://cites.org/sites/default/files/eng/resources/pub/E-Ivory-guide.pdf |
| Period/construction timeline (clasps, findings, settings) | https://www.langantiques.com/university/timeline/ |
| Gold/silver melt calculator | https://meltvalue.com/gold-calculator |

For an independent visual second opinion on an unmarked / can't-place piece, also see
`lib/lens_id.py` (Google Lens) per [`../prompts/identify.md`](../prompts/identify.md) — useful for a
signed-maker design match, far less so for reading an embossed metal stamp (OCR struggles on those).

## Sources

- **US FTC, 16 CFR Part 23** (2018 rev) — the legal definitions for karat, gold-filled, vermeil,
  plate/HGE, and the refuted "gold flashed/washed" terms. The authority for metal claims.
- **GIA — gia.edu** — gem imitation/simulant definitions, diamond simulant tells, and the primary
  jade FTIR study (Fritsch et al.). The authority for gem identification boundaries.
- **International Gem Society — gemsociety.org** — synthetics/assembled-stone/jade-treatment tells.
- **Lang Antiques / Antique Jewelry University** — gold terminology, the maker-mark database
  (Tiffany, Cartier), gem references, and the period timeline.
- **Goldsmiths' Company Assay Office London + The Silver Society** — UK hallmark structure & date letters.
- **CITES** — ivory/tortoiseshell identification (Schreger lines) and legal status.
- **costumejewelrycollectors.com (CJCI)** — costume maker marks; the authority for unsigned "Juliana."
- **The Online Encyclopedia of Silver Marks (925-1000.com)** + **Spratling Silver** — silver, maker,
  and foreign/Mexican assay marks.

> **Open items to deepen in a future revision (v2)** — the honest remaining gaps after four
> research passes (everything marked **[DEEPEN]** above; routed to the reference library in the
> interim, held `[BEST-CASE]`):
> - **Hallmarks:** French poinçons (eagle/owl/Minerva), Italian fascione, Georg Jensen oval +
>   design numbers, and the Asian set (Chinese export, Japanese `JUNGIN`/Occupied Japan, Thai/Siam).
>   *(German, Russian, Mexican, and British are now verified above.)*
> - **Fine makers:** VCA, Bulgari, Boucheron, David Webb, David Yurman, John Hardy, Buccellati,
>   Harry Winston — and the **Cartier serial-number format** (online formats failed verification;
>   do not state one). *(Tiffany, Cartier mark, and Mikimoto are verified above.)*
> - **Costume makers:** Corocraft, Weiss, Schiaparelli, Sherman, Napier, Monet, Sarah Coventry.
>   *(Trifari, Coro/Vendome/Duette, Eisenberg, Schreiner, Miriam Haskell, Hobé, Boucher, Juliana
>   are verified above.)*
> - **Stones/organics:** turquoise, amber, coral, tortoiseshell, emerald, and the common quartz/
>   beryl gems. *(Diamond simulants, corundum, jade, ivory, glass paste, and pearl are verified above.)*
```
