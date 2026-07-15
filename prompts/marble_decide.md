# MARBLE DECIDE — the never-dead-end decision workflow

_Function: take an uncertain marble and **always produce a DIRECTION we can act on**
— without demanding a re-shoot. Re-shoot is the LAST resort, not the first answer._

This exists because the failure mode was: "I can't call it without a better photo."
That is banned here. The camera is not the only sensor — **the user holding the
marble is a better sensor than any photo.** So we convert every "I'd need a photo
of X" into "answer this about X," and we triangulate by **showing reference
marbles** until the user says "that one." We move.

---

## The three hard rules

1. **Always output a DIRECTION.** Every pass ends with a provisional call +
   confidence + the single cheapest next step. Never "undetermined, re-shoot" as
   the whole answer. "Common machine swirl → list as a lot" IS a direction.
2. **Questions before cameras.** Anything I'd want from a photo becomes an
   **in-hand question** (the human-as-sensor bank below). A re-shoot is requested
   ONLY when a question genuinely can't be answered by eye/hand (rare) — and even
   then I still give a provisional direction to act on meanwhile.
3. **Triangulate by showing, not asserting.** When the type is open, show the
   user a **panel of labeled reference marbles** and ask "which is closest?" The
   human's eyes beat CLIP. Narrow, repeat, converge to a direction.

---

## Human-as-sensor question bank

Each row: the photo I used to demand → the question that replaces it → what the
answer resolves. Ask only the 2–3 highest-leverage ones for the specific marble
(the ones whose answer would actually change the direction or value tier).

| Instead of (photo) | Ask (in hand) | Resolves |
|---|---|---|
| Backlit | "Hold it to a bright bulb/window. Does light: (a) pass through clearly, (b) glow but cloudy/milky, (c) stay solid/opaque?" | base type: transparent (swirl/cat's-eye) vs opal/milky vs opaque (patch/Indian) |
| Pole macro | "Look at the two ends/poles. (a) two rough/grainy spots, (b) one rough spot, (c) smooth fold/cut lines, (d) can't tell" | handmade (2 pontils) / single-gather-transitional (1) / machine (seams) |
| Seam shot | "Where the pattern closes — how many lines: 1 or 2? Straight or V/U-shaped?" | maker lean (Master = short V; Akro/Pelt/MK/Vitro = 2 seams) |
| Raking light | "Tilt under ONE light. See: chips/dings? tiny pits? an orange-peel/dimpled skin? or smooth glossy?" | grade + modern-Vacor tell (orange-peel) |
| Oxblood check | "Any band that's a dense dark brick-red that stays dark even held to light — and feels slightly raised/grainy?" | oxblood (value-adder) vs plain red |
| UV shot | "Under a blacklight, does any part glow? What colour — green / yellow / blue / none?" | WV-swirl/JABO/Jackson maker-era clue |
| Caliper | "Measure across: ___ mm or ___ in." | size premium (¾"+ shooter, peewee) — only if it looks off-5/8 |
| Colour (true) | "In daylight: base colour? swirl colours? Are the colour edges CRISP or soft/bleeding?" | crisp non-bleeding → Christensen Agate; soft → common |
| Corkscrew | "Does a ribbon spiral pole-to-pole, and can you find where it STARTS and ENDS?" | Akro corkscrew |
| Cat's-eye | "Is the colour an injected blade/vane in clear glass? How many vanes — 1, 2, 4, cage?" | cat's-eye + tier (multi-vane/cage better) |
| Lutz / mica | "Any GLITTERY metallic gold/copper flecks (Lutz) or SILVERY flecks (mica) in the glass?" | Lutz / mica (premium handmade) |
| Comic / sulphide | "Any printed picture/letters on the surface, or a 3-D figure suspended inside?" | comic / sulphide (rare, pull) |
| Material | "Glass? Or does a magnet grab it (steel), or is it clay/stone?" | non-glass carve-out |
| Count / match | "How many of this exact look? All identical, or each different?" | lot vs single; matched-set premium |

> If the user can't answer one (e.g. no blacklight), it's skipped — never a
> blocker. We proceed on what we have + the visual triangulation.

---

## Visual triangulation (show, don't assert)

When base/type is open after the questions, present a **reference panel** (built
by `tools/marble_typechart.py` / `marble_decide.py` from the LABELED MCSA studio
references — clean, not noisy forum photos). Ask: **"Which of these does yours
look most like?"**

- Round 1 — **base/family chart**: cat's-eye · transparent swirl · opaque patch ·
  milky/opal · handmade core-swirl · slag/transitional · non-glass. User picks a
  lane.
- Round 2 — **within-lane chart** of the named types in that lane (e.g. opaque
  patch → Marble King Rainbow, Vitro patch, Akro patch, Peltier Rainbo). User
  picks closest, or "none of these."
- Stop when the user says "that one" OR after 2 rounds → take the closest +
  "maker uncertain within {lane}." Either way we have a DIRECTION.

The human's "looks like #3" is worth more than any CLIP score and doesn't depend
on photo quality.

---

## Convergence → DIRECTION (the output)

Combine the question answers + the triangulation pick into one of these
directions (always pick the best-supported; never blank):

- **A — Named/premium pull.** A tell + a confident type. → price the named type,
  list aggressively (`[BEST-CASE]`+verify only for the trophy tier).
- **B — Family lot, vintage.** Machine-made, common family (WV swirl / patch /
  cat's-eye), no premium tell. → curated maker-or-theme lot, $ to low-$$.
- **C — Filler.** Modern tells (orange-peel / bag-fresh / dollar-store look) OR
  damaged. → bulk jar/lot.
- **D — Genuinely split, value-moving.** The ONE remaining question would swing
  the tier (e.g. oxblood-or-not) AND can't be answered in hand. → give the
  provisional direction (assume the likely one) AND request the single targeted
  shot — but the lot keeps moving on the assumption meanwhile.

Output per marble: `DIRECTION <A-D>: <provisional call> · confidence <hi/med/lo>
· because <answers/pick> · next: <the one cheapest question or "none — act">`.

## Re-shoot policy (the demotion)

A re-shoot may be NAMED as the ideal, but it is never the stopping point and never
blocks a direction. Order of preference for resolving any uncertainty:
1. an in-hand question (default), 2. a visual triangulation pick, 3. a non-photo
test (magnet/UV/caliper), 4. — only if 1–3 can't and it swings real value — a
single targeted macro, while still acting on the provisional call.
