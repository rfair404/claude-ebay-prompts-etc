# Alternative selling platforms — API feasibility (#166)

PROPOSAL / RESEARCH — no code changes. Answers the question asked in #166:
excluding Facebook, are there other buy/sell apps (the issue names OfferUp)
with "enough API access that this app can run" against them the way it runs
against eBay today?

## What "enough" means for this pipeline

Function 6 (`lib/list_edit.py`) and its setup doc
([`lib/SETUP_EBAY_API.md`](../lib/SETUP_EBAY_API.md)) show what this pipeline
actually needs from a platform, not just "has an API":

- OAuth app + user token — no scripted password login
- create an **unpublished** offer/draft (the REVIEW gate needs a syncable,
  not-yet-live state to hold before human approval)
- upload photos server-side (eBay: EPS)
- publish the offer programmatically once approved
- read sold/comp data for PRICE
- read order/fulfillment data for REPORT

A platform without a public, ToS-legitimate listing-*write* API fails
regardless of how good its resale market is — the no-automatic-publish
firewall this pipeline is built around only works if the write path is one
the platform actually sanctions.

## Platforms surveyed

| Platform | Public listing-write API | Verdict |
|---|---|---|
| OfferUp | None for third-party sellers; scripted posting is done via browser automation against the consumer app, not a sanctioned API | No |
| Poshmark | None; ToS treats bot/reseller-automation tools as prohibited | No |
| Mercari (US) | No self-serve public listing API for individual sellers (partner-only integrations historically, not open to us) | No |
| Depop (Etsy-owned) | No public listing API for individual sellers | No |
| Vinted | No public API; only reverse-engineered/unofficial endpoints exist, which is a ToS violation and a ban risk | No |
| Grailed | No public API | No |
| Kidizen | No public API | No |
| Whatnot | Livestream-auction format; no listing-creation API and doesn't fit a static-listing pipeline anyway | No |
| Craigslist | No API by design; posting automation is the canonical way to get an IP/account banned | No |
| Nextdoor (For Sale & Free) | No API | No |
| **Etsy** | **Open API v3** — OAuth2, draft listing creation, image upload, receipts/orders | Yes, with a category fence: listings must be handmade, craft supplies, or 20+ years old ("vintage") |
| **Reverb** (music gear) | Public REST API with OAuth, built for third-party seller tools, supports listing creation | Yes, for gear that fits the specialization |
| Bonanza | Public API aimed at multi-channel sellers, similar posture to eBay's | Plausible, not evaluated in depth here |

## Recommendation

Nothing in the "like OfferUp" bucket named in the issue — OfferUp, Poshmark,
Mercari, Depop, Vinted, Grailed, Kidizen — has a public listing API. Building
against their private endpoints would trade this pipeline's core invariant
(an explicit, auditable, ToS-respecting publish path, gated on human
approval) for one that risks the account outright. Not recommended.

The two credible expansions are:

1. **Reverb**, for shoots that fall in the music-gear specialization.
2. **Etsy**, for items that are genuinely vintage (20+ years) or
   handmade-adjacent — gated on category fit during IDENTIFY, since not
   every shoot would qualify.

Bonanza is worth a follow-up look if volume ever justifies a second
general-goods marketplace, but hasn't been evaluated to the same depth as
the other two here.

## If a second platform is built (v5, not this issue)

- A new `lib/<platform>_client.py` mirroring `ebay_client.py`'s OAuth +
  offer-creation shape.
- A new `SETUP_<PLATFORM>_API.md` alongside `lib/SETUP_EBAY_API.md`.
- Function 6 needs a `--platform` flag or a per-shoot target field, since
  `draft.md`'s schema currently assumes eBay's category/item-specifics
  vocabulary.
- PRICE needs a platform-specific sold-comps source; Reverb's and Etsy's
  sold data isn't reachable the same way eBay's Browse API + the logged-in
  sold-browse stage are.

That's a real feature, not a drop-in — this doc closes #166 as research so
any future work starts from a scoped choice (Reverb and/or Etsy) instead of
re-surveying the market blind.
