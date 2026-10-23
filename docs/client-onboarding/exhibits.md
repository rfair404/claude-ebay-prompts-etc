---
version: 1.0.0
updated: 2026-10-23
---

# Exhibits A and B

DRAFT. This is a template, not legal advice. Have it reviewed by a Georgia-licensed attorney before use.

<!-- TODO(Russell): confirm the DBA name on client paperwork. -->

These exhibits attach to the [Consignment Agreement](consignment-agreement.md) and the [Purchase Agreement and Bill of Sale](purchase-agreement.md) between you and Russell Fair Enterprises, LLC d/b/a The Fair Hand. There is one copy of each here so the two agreements always use the same forms.

## Exhibit A: Item schedule

Client code: K____ Date: __________ Agreement: [ ] Consignment [ ] Purchase Sheet ____ of ____

**How to fill it in:** one row per item, or per group of identical items. Describe each item, including its condition and any marks, chips, cracks, repairs or missing parts. For identical items, use one row and put the count in `qty`. Every row must be different from the others.

- **Consignment:** use `floor` if you want a lowest price for an item. Leave `purchase_price` blank.
- **Purchase:** put a price per line in `purchase_price`, or leave it blank and write the lot total at the bottom. Leave `floor` blank.
- **Office use** columns are filled in by us after intake.

| # | description | qty | why | floor | purchase_price | bin (office) | cut (office) | est_low (office) | est_high (office) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | | | | | | | | | |
| 2 | | | | | | | | | |
| 3 | | | | | | | | | |
| 4 | | | | | | | | | |
| 5 | | | | | | | | | |
| 6 | | | | | | | | | |
| 7 | | | | | | | | | |
| 8 | | | | | | | | | |
| 9 | | | | | | | | | |
| 10 | | | | | | | | | |
| 11 | | | | | | | | | |
| 12 | | | | | | | | | |
| 13 | | | | | | | | | |
| 14 | | | | | | | | | |
| 15 | | | | | | | | | |

More items? Continue on another sheet.

Purchase lot total (if not priced per line): $__________

Other costs you approve to come out of Net Proceeds (consignment only): __________

Heavy, oversized or fragile items (row numbers): __________

Client initials: ______ Our initials: ______

**Column key:** `description` is required. `why` is your notes: where the item came from, its history, anything we should know. `cut` is LIST (sell on its own), LOT (sell in a group) or RETURN (we give it back to you).

<!-- Internal notes for whoever types this into the import file:
- Keep these exact header names. `python -m lib.cli consign import` reads any column whose name matches a `client_ledger.csv` column, and ignores columns that don't match (# / floor / purchase_price).
- Never add a `state`, `ask` or `buyout_price` column to Exhibit A (or rename purchase_price to one): import would read it into the consignment ledger.
- Import skips a row whose bin + description match another row. Use qty for identical items.
- The ledger has no floor column yet, so a consignor floor lives only on this paper sheet. Handoff to developer.
-->

## Exhibit B: Certification of authority

Sign this if you are signing for a trust, an estate, another person or a company.

**I am signing as** (tick one):

- [ ] Trustee of the __________ Trust dated __________
- [ ] Executor of the Estate of __________
- [ ] Administrator of the Estate of __________
- [ ] Agent under a power of attorney signed by __________ dated __________
- [ ] Officer or manager of __________ (legal name as on the Secretary of State record), title __________

**Proof attached** (tick each):

- [ ] Certification of trust (or the trust pages naming the trustee and the power to sell)
- [ ] Letters testamentary
- [ ] Letters of administration
- [ ] Power of attorney
- [ ] Entity resolution or certificate of authority naming who may sign
- [ ] Secretary of State record, state of __________, checked on __________

**I certify that:**

1. The document checked above gives me the authority to sell or consign the items on Exhibit A.
2. That authority has not been revoked, suspended or limited in a way that affects this sale or consignment.
3. If anyone else must also sign (a co-trustee, co-executor or second officer), they have signed below.
4. For a power of attorney: the person who signed it is living, and it is still in effect.
5. For a company: it exists and is in good standing in its home state.
6. I will tell you in writing right away if any of this changes.

Signature: __________ Printed name: __________ Date: __________

Co-signer (if needed): __________ Printed name: __________ Date: __________

**A note for heirs:** If you inherited items but no court has appointed you (no letters testamentary or of administration) and there is no court order, we can't accept the items without our counsel's sign-off.
