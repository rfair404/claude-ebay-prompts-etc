# condition-rubric — structured condition analysis

Shared by IDENTIFY (quick condition notes) and INVESTIGATE (defensible
condition claims + eBay grade). The point: stop writing vague "good
vintage condition" and instead inspect against a per-material defect
checklist, then map to an eBay grade with a conservative tie-break.

Condition drives returns more than any other field. A missed chip or an
over-stated grade causes an INAD return; an honest, specific disclosure
prevents it. Dig in.

---

## Method

1. **Inspect every photo at the defect level**, not the gestalt level.
   Zoom mentally into edges, corners, contact points, seams, mechanisms.
2. **Run the material checklist** for the item's dominant material(s).
3. **Record each defect** with location + severity (minor / moderate /
   significant) + photo reference. No defect is too small to note.
4. **State what you cannot assess** from the photos (function untested,
   undersides not shown, interior not visible) — silence reads as a
   claim of "fine".
5. **Map to one eBay grade** using the tie-break rule below.

## Material defect checklists

Inspect for these; report the ones present and explicitly clear the ones
checked-and-absent only when it matters to the grade.

- **Paper / ephemera / books** — foxing, tanning/toning, edge wear,
  corner creases/dog-ears, spine roll/cracking, tears, tape/repairs,
  writing/stamps/inscriptions, water/damp staining, missing pages,
  loose/detached pages, sun-fade.
- **Ceramic / pottery / porcelain** — chips (rim, foot, spout, handle),
  hairline cracks (backlight/ring-test not possible from photo — flag),
  crazing, glaze loss/flaking, repairs/restoration, stain absorption,
  manufacturing flaws vs damage, missing lid/components.
- **Glass / crystal** — chips, flea-bites, fleabite clusters at rim,
  cracks, scratches, cloudiness/sickness, internal fractures, base wear
  (genuine age sign), repairs.
- **Metal** — rust/oxidation, pitting, patina (often desirable — say
  so), tarnish, dents, bends, scratches, plating loss, solder repairs,
  missing/replaced hardware, seized mechanisms.
- **Textile / clothing** — moth holes, staining (type/location), pilling,
  fading, stretching, seam separation, missing buttons/zippers, hem
  damage, odor (cannot assess from photo — flag), alterations, label
  legibility/dating, fiber pilling.
- **Wood / furniture** — scratches, gouges, water rings, veneer
  lifting/loss, joint looseness, warping, finish wear/checking, woodworm,
  replaced parts, structural integrity (flag if not verifiable).
- **Electronics / mechanical** — physical wear; **functional status is
  NOT claimable from photos** unless a photo shows it powered/operating.
  Default to "untested — sold as-is for parts or repair" unless visible
  operating evidence exists. Missing cords/accessories, corrosion in
  battery compartments, cracked screens.
- **Jewelry** — stone chips/looseness, clasp function (flag if
  unverifiable), plating wear, hallmark legibility, kinks, missing
  stones, repair solder, metal test not possible from photo (flag).

## eBay condition grade mapping

Map the inspection to one `CONDITION_ENUM` value (from
`v2/lib/ebay_schema.py`):

| Evidence | Grade |
|---|---|
| New, unused, sealed, or mint unworn | `NEW` |
| As-new, minor handling only | `LIKE_NEW` |
| New but packaging opened | `NEW_OTHER` |
| Light wear, fully sound, no notable defects | `USED_EXCELLENT` |
| Light–moderate wear, sound, minor defects noted | `USED_VERY_GOOD` |
| Moderate wear, intact, functional | `USED_GOOD` |
| Heavy wear / multiple defects, intact | `USED_ACCEPTABLE` |
| Not working / untested / incomplete / for parts | `FOR_PARTS_OR_NOT_WORKING` |

**Tie-break (mandatory): when between two grades, pick the LOWER one.**
Buyer surprise on the upside is fine; downside surprise triggers returns.
Note the choice (and the grade you passed up) wherever the phase records
its decisions.

## Output

- **IDENTIFY** `Condition` field: bullet observations with location +
  severity; one line on what can't be assessed. Not prose.
- **INVESTIGATE**: defects feed "Directly observable"; the grade + a
  one-line condition summary feed "Listing-safe claims"; un-assessables
  go in "NOT defensible". DRAFT writes every flagged defect into the
  listing's Condition section — defects always survive any trim.
