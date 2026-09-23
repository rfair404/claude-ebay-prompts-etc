# Maker's marks — identification, sources & the local index — KB article

```yaml
version: 1
last_reviewed: 2026-09-22
coverage: >
  SOURCE-VERIFIED: the print canon; the National Stamping Act trademark duty and
  its 1961 non-retroactive start; the 1896/1922 Jewelers' Circular registers as
  the legally-clean image corpus; Lang/AJU's facet scheme and its 41-term punch
  OUTLINE SHAPE vocabulary (read off the live DOM); the harvestability and
  licensing position of every candidate source.
  NOT ESTABLISHED, do not infer from this article: the per-maker roster for
  1930s-1980s vintage houses (Krementz, Carl Art, Uncas, Coro, Trifari, Monet,
  Napier, Ostby & Barton, Van Dell, Winard, Bates & Bacon, Symmetalic, Michael
  Anthony, Milor, ArtCarved) with mark appearance and date ranges; and the
  practical technique for reading a worn or RAISED CAST mark. Both were asked
  and both came back with zero surviving claims. See "Known gaps" below.
related:
  - ../articles/silver-hallmarks.md   # national SYSTEMS decode — this article does not repeat it
  - ../../specializations/jewelry.md  # category playbook
```

## When to consult this

An item carries a maker's mark, sponsor mark or trademark and you want to know
(a) whose it is, (b) whether that changes the price, and (c) which reference to
reach for. For decoding a national hallmarking *system* — British date letters,
Continental fineness, assay offices — go to
[silver-hallmarks.md](silver-hallmarks.md) instead; this article is about
identifying the **maker** and about the local lookup index.

---

## 1. The rule that decides whether a maker mark should even be there

US federal law (National Stamping Act, 15 U.S.C. 297; restated in the FTC Guides
at 16 CFR 23.10 Note 2) requires that whoever applies a karat or fineness mark to
a gold or silver article also apply their own registered-or-applied-for trademark
**or their name**, in type at least as large as the quality mark and as close to
it as possible.

Four qualifications matter more than the rule:

- **It starts in 1962, not 1906.** The trademark duty was added by Pub. L. 87-354
  (enacted 4 Oct 1961), effective the first day of the third month after
  enactment, reaching only articles made or imported after that date. It is **not
  retroactive.** So for the pre-1962 antique cohort there is no statutory reason
  to expect a maker's mark beside a karat stamp, and its absence proves nothing.
- **A bare name satisfies it.** "Registered trademark" overstates the duty — a
  name works, and a trademark need only be *applied for* within 30 days of the
  article entering commerce.
- **The duty attaches to whoever applies the quality mark** (or imports a marked
  article). Unmarked, worn, and non-compliant imported pieces fall outside it.
- **The FTC Guides are advisory.** The operative law is the statute; the CFR Note
  is descriptive. And 16 CFR 23.10 is **not** a cataloguing vocabulary — a claim
  that it supplies one was tested and refuted.

Practical read: on a **post-1962 US piece**, a karat stamp with no maker mark
anywhere is mildly odd and worth a second look for a rubbed mark. On a
**pre-1962** piece, it is unremarkable.

---

## 2. The three tiers of source, and how to use each

### Tier 1 — the print canon (attribution authority)

Independently corroborated by GIA's Richard T. Liddicoat Gemological Library
hallmarks bibliography:

| Work | Scope | Catch |
|---|---|---|
| **Tardy**, *International Hallmarks on Silver* (and companion gold volume) | World marks, by country | **Split by metal.** The commonly-bought silver Tardy leaves gold, platinum and palladium uncovered — buying one does not cover the other. Cite Tardy's *structure*, never a specific edition year: the silver volume has run to at least a 22nd edition and the "current marks" volumes are stale snapshots. |
| **Jackson's**, *Silver & Gold Marks of England, Scotland & Ireland* | British | The standard for British assay decode |
| **Rainwater**, *American Jewelry Manufacturers* (Schiffer) | US makers | The one monograph specifically for US jewelry (as opposed to silver) manufacturers |

GIA's bibliography itself is a caveat: its newest entry is 2012, it carries an
error GIA flags internally, and it omits every free online database. It is a
print-literature reading list, not a current resource guide.

### Tier 2 — free online databases (lead generators only)

All three are dealer- or hobbyist-maintained, **all-rights-reserved**, and none
publishes an editorial process, citation trail or per-entry revision dates.

- **Lang Antiques / Antique Jewelry University (AJU)** — the strongest free tool
  found. Genuinely faceted (see §3). ~240 pages of circa-dated entries. AJAX-only
  search, no URL parameters, no API, no bulk export.
- **Global Gemology & Appraisals, A-Z Jeweler Trademark Directory** — one combined
  alphabetical register spanning jewelers, manufacturers, silversmiths and
  watchmakers. Retrieved **only** by maker name alphabetically; there is no
  lookup by shape, device or letters. Its "massive database" self-description is
  puffery — a sampled per-letter page held roughly 20 entries.
- **925-1000.com** (Online Encyclopedia of Silver Marks) — widely cited, but it
  serves bare images with essentially no alt text or device descriptions, and it
  403s automated clients.

**Use them to generate a candidate, never to settle one.** Corroborate against
Tier 1 or the registers in §4 before asserting a maker.

### Tier 3 — trademark registers (authoritative but narrow)

- **USPTO TSDR API** — offers programmatic per-mark image retrieval, but is
  key-gated behind account registration (API key required since Oct 2020; ID.me
  + MFA; ODP sign-in mandatory as of 18 June 2026), and indexes **only
  registered** marks, so it misses most antique and vintage maker's marks.
  There is **no** free bulk TIFF corpus of registration images 1870-present —
  that was tested and refuted; do not plan around it.

---

## 3. The descriptor vocabulary (this is the lookup schema)

AJU's facet scheme is the best-designed found and is what the local index
adopts: free text **plus** five dropdowns — **Alpha, Symbol, Outline Shape,
Country, City**.

The key design decision worth copying: **punch outline is a first-class,
separately filterable key**, distinct from the pictorial Symbol taxonomy (AJU
carries "lozenge" twice under different term IDs — once as a Symbol, once as an
Outline Shape). That matters because *a mark whose letters are illegible can
still be looked up by its outline and its device.*

**OUTLINE SHAPE — the 41-term controlled vocabulary, verbatim:**

> arc · badge · bone · Book · bugle · cartouche · chain · circle · crest · cross ·
> curve · cylinder · diamond · double circle · ellipse · emblem · frame · heart ·
> hexagon · horn · kite · ladder · leaf · loaf · lozenge · marquise · navette ·
> octagon · oval · parallelogram · pear shape · pillow · rectangle · rhombus ·
> shape · shield · square · star · trapezoid · triangle · v clipped

Two real limits, carried over honestly:

- The vocabulary is **not fully normalized** — it mixes true punch outlines
  (lozenge, kite, pillow, v clipped) with generic containers (shape, frame,
  cartouche, emblem). Expect to pick the most specific term and accept overlap.
- **City/Country describe where the maker WORKED**, which AJU warns is "not
  usually contained within the mark." Treat provenance facets as an external
  inference, never as a reading of the punch.

Free-text recall is bounded by the cataloguer's own wording — a descriptor only
matches records that actually carry it. Write descriptors generously.

---

## 4. The image corpora, and what may legally be ingested

| Corpus | Licence | Harvestable? |
|---|---|---|
| **Jewelers' Circular, *Trade-marks of the jewelry and kindred trades*, 1896 ed.** — Internet Archive `TradeMarksJewelryAndKindredTrades` | **CC0 Public Domain Mark 1.0**, publisher Jewelers' Circular Publishing | **YES.** 220 leaves, page JPGs and JP2/OCR via the standard IA download paths, no auth, no rate wall. This is what the local index is seeded from. |
| Same work, **1922 4th ed.** — HathiTrust `hvd.hb9jsx` (Harvard) | Public domain in the US (`rightsCode: pd`) | **In principle**, but `babel.hathitrust.org/cgi/imgsrv` returns **403 to automated clients** (verified), whole-volume PDF is gated to member-institution logins, and the Google-digitized scan carries Google's non-commercial-use request. **Do not build a scraper for it.** |
| Jewelers' Circular **1950 6th ed.** | **NOT public domain** | No. The compilation-copyright ruling it won (*Jeweler's Circular Pub. Co. v. Keystone*, 281 F. 83) was overruled by *Feist* (1991), but the 1950 imprint's own term has not expired. |
| **AJU / Lang**, **Global Gemology** | All rights reserved, individually attributed photographers | No. Not licensed for mirroring. |
| **Wikimedia Commons, Category:Hallmarks** | Free licences (per-file) | Yes, per-file — a secondary top-up source, licence must be checked per image. |

Why the 1896/1922 registers are uniquely valuable: they record **unregistered**
marks, which by definition never appear in any trademark database. The catch is
that they are **trade-submitted, not an exhaustive census** — entries came from
cuts and clippings supplied by the firms themselves, so **absence is not evidence
a mark does not exist.**

**The date ceiling is the real limitation.** 1896 (and even 1922) predates
Trifari, signed Monet and Napier, Coro's later marks, Michael Anthony (1977),
Milor and ArtCarved. The corpus is strong for **antique** marks and materially
incomplete for the **1930s–1980s vintage** marks that dominate a typical estate
lot. Do not let a confident-looking index hit on a 19th-century mark stand in for
a mid-century one.

---

## 5. Does an attributed mark actually move the price?

**Test it against the market before it touches a title.** An attribution only
pays if buyers search the name:

- eBay **SOLD**: `"<MARK>" <metal/category>` — how many, and are they real hits?
- eBay **ACTIVE**: `"<MARK>" <category> marked` — how many?

A real manufacturer mark leaves a trail. Measured 2026-09-22 on a 14K pendant:
`Samuel Aaron` surfaced as an eBay *brand* with priced listings, while a proposed
`CVM` returned 1 sold (a false positive — a seller's own SKU suffix) and 0 active,
with no register match anywhere. No footprint means no premium at any reading of
the letters.

No footprint → list as Unbranded, record the stamp in the body as an
unattributed maker's mark, and keep the string out of the title. Unsearched
characters are wasted title space, and an unverified attribution is a claim we
cannot back.

---

## 6. Reading the local index's scores — cosine lies

`tools/mark_lookup.py` queries `kb/index/jewelry_marks/` (1,086 cuts from the
1896 register). CLIP cosines are **not calibrated** and read far higher than
intuition expects, so a raw score is worse than useless on its own — it is
actively misleading. Measured on this index:

| Query | Top | Runner-up | Gap | Truth |
|---|---|---|---|---|
| A modern 14K pendant stamp | 0.724 | 0.723 | **0.001** | not in the register at all |
| A crop taken from the index itself | 0.987 | 0.814 | **0.174** | exact match |

0.724 *looks* like a strong hit. It is background. **The discriminating signal
is the gap to the runner-up, not the absolute score** — a real match stands
clear of second place, noise does not. (A z-score against the query's own
distribution is not enough either: it rated the identical crop "weak", because
an exact match also drags its neighbours up and fattens the spread.)

The tool prints this verdict itself, so read the verdict line, not the number.

## 7. Known gaps — do not paper over these

Two of the six research goals produced **zero** surviving claims. They are open,
not answered negatively:

1. **The vintage maker roster.** What the marks of Krementz, Carl Art, Uncas,
   Coro, Trifari, Monet, Napier, Ostby & Barton, Van Dell, Winard, Bates &
   Bacon, Symmetalic, Michael Anthony, Milor and ArtCarved actually look like,
   and their date ranges. This is exactly the 1930s–1980s band the 1896 corpus
   misses, so the gap compounds.
2. **Reading a worn or RAISED CAST mark.** Cast marks are rounded and blurred
   where struck marks are crisp; lighting angle, loupe power, and what to do
   when a mark is simply illegible were all unanswered.

Also unresolved: whether Lang would license AJU images for a private
non-republished index, and the decoding *mechanics* (as opposed to bibliography)
for French, German, Italian, Scandinavian, Mexican and Russian systems — for
those, [silver-hallmarks.md](silver-hallmarks.md) is the current best local source.

---

## How it maps onto our fields

- A mark you cannot attribute → Brand `Unbranded`; describe the stamp in the
  body; **do not** put the letters in the title.
- A mark you can attribute *and* that has a sold-listing footprint → Brand = the
  maker; the name earns title space; re-comp against that maker's cohort.
- Never let an index hit upgrade a `[BEST-CASE]` into a claim. Retrieval is a
  candidate finder. The same honesty rules as the marble index apply.

## Sources

- GIA, Richard T. Liddicoat Gemological Library hallmarks bibliography — https://www.gia.edu/gia-news-research-hallmarks-bibliography
- Lang Antiques / AJU, Makers Marks database — https://www.langantiques.com/university/makers-marks-2/
- Lang Antiques, hallmarks & makers marks resource list — https://www.langantiques.com/university/hallmarks-makers-marks-resource-list/
- eCFR, 16 CFR Part 23 (FTC Jewelry Guides) — https://www.ecfr.gov/current/title-16/chapter-I/subchapter-B/part-23
- 15 U.S.C. 297 (National Stamping Act) — https://uscode.house.gov/
- Internet Archive, *Trade-marks of the jewelry and kindred trades* (1896) — https://archive.org/details/TradeMarksJewelryAndKindredTrades
- HathiTrust, same work 1922 4th ed., Harvard copy — https://catalog.hathitrust.org/Record/100479417
- Global Gemology, A-Z Jeweler Trademark Directory — https://www.globalgemology.com/jeweler-trademark-directory.html
- USPTO bulk data — https://data.uspto.gov/bulkdata
- Wikimedia Commons, Category:Hallmarks — https://commons.wikimedia.org/wiki/Category:Hallmarks
