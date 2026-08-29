# Specializations — category-expert knowledge modules

A **specialization** is a self-contained expert field guide for one category
of item (marbles, fountain pens, pocket knives…). When IDENTIFY meets an item
whose category matches a module's triggers, it **loads that module and applies
its rules** — turning a generalist identification into a specialist one:
better attribution, the named high-value types flagged, the right condition
vocabulary, the inspection shots a specialist would ask for, and a coarse
value tier so PRICE and CURATE start from an informed anchor.

This is prompt-driven, like the rest of the pipeline — a module is just a
Markdown file the pipeline reads on demand. No code, no build step. Adding the
next specialization is: drop a new file in here, add one row to the registry
below.

---

## Registry (the index IDENTIFY checks)

IDENTIFY reads THIS table to decide whether a specialization applies. Match an
item's category/material against the **Triggers** column; on a hit, load that
module before settling the item's fields. Keep this table in sync with the
files in this directory — it is the single source of truth for what's active.

| Module | Triggers (category / material / keyword match) | Status |
|---|---|---|
| [marbles.md](marbles.md) | marble, marbles, shooter, swirl, sulphide, agate (as a marble), "glass ball" toy, marble lot/jar/collection | active |
| [jewelry.md](jewelry.md) | ring, necklace, pendant, locket, bracelet, bangle, brooch/pin, earrings, cufflinks, chain, charm, parure; precious-metal marks (10k/14k/18k, 375/585/750, sterling/925, plat/950, "gold filled"/GF/vermeil); costume jewelry, rhinestone, signed maker (Tiffany/Cartier/Trifari/etc.); a gemstone **set in / sold as jewelry** (diamond, sapphire, jade, agate-as-gemstone, etc.) | active (v1, partial — see module header) |
| [silverplate.md](silverplate.md) | silverplate/"silver plate"/EPNS/EP/A1/AA/"triple plate"/"quadruple plate"; plated flatware & serving pieces & hollowware; "service for 8/12", flatware set, gravy ladle, cold-meat fork, sugar shell; maker lines 1847 Rogers Bros / Wm Rogers (& Son) / Wm A Rogers / Holmes & Edwards / Oneida Community / Tudor Plate / International Silver (`IS`). **Carve-out:** solid STERLING/925 flatware → jewelry.md silver-decode + PRICE sterling rules, NOT here | active (v1) |

_Add a row per new module. Triggers should be specific enough not to fire on
unrelated items (e.g. "agate" alone is also a gemstone — qualify it)._

---

## How IDENTIFY uses a module (the contract)

1. **Detect.** After forming a first-pass Category for an item, scan this
   registry's Triggers. On a match, the item is *in specialization* — load the
   module file.
2. **Apply.** Use the module's taxonomy, value drivers, condition vocabulary,
   and authentication tests to settle Brand / Type / Era / Condition and the
   distinguishing-marks field. The module **refines**, never overrides, the
   honesty rules in [`../prompts/_shared.md`](../prompts/_shared.md) and the
   maker-attribution discipline in
   [`../prompts/identify.md`](../prompts/identify.md): still no inventing, still
   `[BEST-CASE]` + scenario brackets for value-swing inferences.
3. **Flag value.** Emit the module's **value tier** for the item (its coarse
   triage band) in Distinguishing marks, so PRICE/CURATE know which items
   deserve the exact-comp hunt and which are filler.
4. **Request the right shots.** If the confident call needs an inspection the
   photos don't show, set `needs_followup_photo` to the SPECIFIC shot the
   module names (e.g. for marbles, a pontil/seam macro), not a generic "more
   photos".

A specialization adds expertise; it does not change the gate contract. The
maker-mark stop-and-ask, the SOFT-gate defaults, and the publish firewall all
still apply exactly as in [`../RUN.md`](../RUN.md).

---

## Module file schema

Every module follows [`_template.md`](_template.md) so they stay
interchangeable. Required sections:

- **Front matter** — `triggers`, `version`, `last_reviewed`, source list.
- **When this applies** — the precise trigger conditions + carve-outs.
- **Fast triage** — the 30-second-per-item pass for sorting a big lot into
  "look closer" vs "filler". This is the workhorse for a 1000+ item pile.
- **Taxonomy** — makers/types and how to recognize each.
- **The money types** — the named high-value items and their tells.
- **Value drivers** — what moves price, in priority order.
- **Condition grading** — the category's own vocabulary + how defects cut value.
- **Authentication & fakes** — antique vs modern/repro; common forgeries.
- **Price tiers** — coarse bands ($ / $$ / $$$ / $$$$) with what lands in each.
- **Inspection shots** — the macro/lighting the specialist asks for.
- **Output hooks** — how the module's findings map onto IDENTIFY's fields.
- **Sources** — the reputable references behind the module.

Keep modules **evidence-based and dated**: cite the source class (specialist
society, auction house, standard reference book) and record `last_reviewed` so
stale market claims can be refreshed. Prices are tiers and signals, not
quotes — the live PRICE phase still hunts real comps.

---

## Adding a new specialization

1. Copy `_template.md` to `<category>.md`.
2. Research it from reputable, specialist sources (society/club references,
   established auction houses, standard reference books — not random listings).
   The `deep-research` skill is a good way to gather and fact-check the body.
3. Fill every section; set `version: 1` and today's `last_reviewed`.
4. Add a row to the registry table above with specific triggers.
5. That's it — IDENTIFY will pick it up on the next run that hits the triggers.
