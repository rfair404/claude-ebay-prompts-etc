━━ REVIEW: 3.5 Gallon Benchtop Parts Washer (sku 016fec0d · ledger DRAFTED) ━━

Title [74/80]: 3.5 Gallon Benchtop Parts Washer w/ Electric Solvent Pump - Untested AS-IS
Price:        $35.00  (FIXED_PRICE · provisional working price = PRICE Recommended tier)
Best Offer:   OFF  (price == Recommended tier; no negotiation headroom)
Condition:    FOR_PARTS_OR_NOT_WORKING  (electric pump never shown powered → untested; lower-grade tie-break)
Quantity:     1  (single)
Photos:       4  (hero = 02 clear interior+pump; 01 motion-blurred, placed last)

Preflight (assembled by hand — API unavailable, not the --review command):
  · condition  : FOR_PARTS_OR_NOT_WORKING — accepted by Parts Washers category (verify at sync)
  · shipping   : CALCULATED via UPSGround, packed 22 lb / 20×17×12 in — NOT free
                 (15-20 lb steel unit ships ~$18-28; free shipping would erase a $35 sale)
  · insurance  : not flagged (item < $100)
  · category   : Business & Industrial > Automotive Tools > Shop Equipment > Parts Washers (category_id blank — eBay suggests at list)

Comps (open to verify — NO exact sold comp captured; Apify + Chrome unavailable this run):
  Near-exact (same model family, used):
    • Vintage Chicago Electric Portable Parts Washer 3.5 Gal — https://www.ebay.com/itm/295778799035
    • Harbor Freight Chicago 35740 (spec/model match) — https://www.ebay.com/p/1842326524
  Ceiling / context (NEW asking):
    • $97 — 3.5 Gal Portable Parts Washer Electric Pump — https://www.ebay.com/p/571551760
    • $29.99 — 3.5 Gal Automotive Parts Washer New — https://www.ebay.com/itm/175636482472
  All sold results: https://www.ebay.com/sch/i.html?_nkw=3.5+gallon+parts+washer&LH_Sold=1&LH_Complete=1&_sop=3
  Tiers: Conservative $20 · Recommended $35 · Push-high $50 (only if pump confirmed working + complete)

Condition detail (verbatim — every flagged defect, not softened):
  • Electric pump UNTESTED — not shown running, no working guarantee; sold for parts or repair.
  • Tank dirty inside and out; sediment and debris in the basin — needs cleaning.
  • Red enamel paint scuffed and scratched throughout; no through-rust seen.
  • Pump unit + supply hose present (shown sitting in the basin).
  • Lid, fusible link, flexible nozzle/spigot, power cord/switch NOT clearly pictured — not guaranteed present/intact.
  • Ships EMPTY — no solvent or cleaning fluid included.

⚠ Needs review / manual intervention (from NEEDS_REVIEW.md):
  1. Brand left "Unknown" — form/specs match Chicago Electric / HF 35740 family; confirm if known (title SEO).
  2. PRICE $35 is provisional — NO exact sold comp; Apify (Stage B) + Chrome (Stage C) both UNAVAILABLE here.
     Research incomplete on direct-eBay sold data. Confirm/refine price before publish.
  3. Shipping: chose CALCULATED + local-pickup-friendly over free (weight-to-value). Confirm preference.
  4. Pump working status unknown → graded FOR PARTS. A powered-on test photo could lift grade + price (~$50).
  5. Completeness (lid/nozzle/cord/fusible link) unconfirmed — listing claims only the tank + pump shown.
  6. Follow-up photos suggested: full exterior + lid + cord, the flexible nozzle, pump running.

→ Approve publishes this LIVE at $35.00. On your explicit approval I would run:
      python lib/list_edit.py --list samples/parts-washer-3p5gal --confirm
  NOTE: publishing also requires eBay API credentials (~/.ebaybiz/config.yaml), which are NOT set in this
  environment — so even on approval, a live publish cannot complete here until credentials are configured
  (see lib/SETUP_EBAY_API.md). The draft, comps, and card are complete and ready.
