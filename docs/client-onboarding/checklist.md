---
version: 1.0.0
updated: 2026-10-23
---

# Client onboarding checklist

DRAFT. This is a template, not legal advice. Have it reviewed by a Georgia-licensed attorney before use.

Client code: K____ (we assign it) Date: __________ Taken by: __________

Deal type: [ ] Direct purchase [ ] Consignment [ ] Consignment with buyout
Client type: [ ] Individual [ ] Trust or other fiduciary [ ] Company

Paperwork: [Consignment agreement](consignment-agreement.md) · [Purchase agreement / bill of sale](purchase-agreement.md) · [Exhibits A and B](exhibits.md)

| Done | What we need on file | Direct purchase | Consignment | Consignment with buyout | Individual | Trust or fiduciary | Company |
|---|---|---|---|---|---|---|---|
| [ ] | Contact info: name, phone, email, mailing address | Yes | Yes | Yes | Yes | Yes (signer and trust/estate) | Yes (signer and company) |
| [ ] | Photo ID seen. Record the ID type only, never the number | Yes | Yes | Yes | Client's ID | Signer's ID | Signer's ID |
| [ ] | Ownership or authority | Yes | Yes | Yes | Ownership statement in the signed agreement | Exhibit B + certification of trust, letters testamentary or of administration, or power of attorney | Exhibit B + legal name matching the Secretary of State record + who may sign |
| [ ] | IRS Form W-9 | Not needed (CPA to confirm) | Before first payout | Before first payout | Yes | Yes (trust/estate's) | Yes (company's) |
| [ ] | Payment or payout method | Payment method and reference | Payout method | Both | Yes | Yes | Yes |
| [ ] | Signed paperwork with Exhibit A | Bill of sale | Consignment agreement | Consignment agreement + written buyout for each item bought | Yes | Yes | Yes |
| [ ] | Red-flag check done; restricted items set aside | Yes | Yes | Yes | Yes | Yes | Yes |
| [ ] | Heavy, oversized or fragile items marked on Exhibit A | Yes | Yes | Yes | Yes | Yes | Yes |
| [ ] | Copy of everything signed given to the client | Yes | Yes | Yes | Yes | Yes | Yes |

**Red flags:** fresh retail tags or many of the same item, removed or filed-off serial numbers, can't explain where items came from, pressure for quick cash, liens or loans on the items. Stop and ask before going ahead. Heirs with no letters or court order: see the note on [Exhibit B](exhibits.md#exhibit-b-certification-of-authority).

## Office use: where things are recorded

Commands for each step are in [RUN.md, "Consignment (`consign`)"](../../RUN.md).

- **Client name and contact:** only in `inventory/_consign/clients.csv`. Never in git, a PR, an issue or a brief. Everywhere else, use the K## code.
- **Consignment items:** imported into the intake's ledger with no spend.
- **Purchases:** `python -m lib.cli context <folder> --spend <amount>`. Record the lot price once.
- **Buyouts:** `python -m lib.cli context <item folder> --spend <buyout_price> --spend-unit item` (our cost basis), and set its row to `EXCLUDED` with the same `buyout_price` in the intake's `client_ledger.csv` (what we owe the client). These are two sides of one purchase, not two costs: the accountant books one inventory purchase at `buyout_price` when Russell pays, and nothing to "Due to consignors".
- **Signed paper, ID-check notes and W-9s:** kept outside the repo. <!-- TODO(TD/accountant): decide where signed paperwork is stored. -->
- **Purchase packet** (date, amount, payment method with last 4 only, receipt) goes to the accountant, for direct purchases and buyouts alike.
