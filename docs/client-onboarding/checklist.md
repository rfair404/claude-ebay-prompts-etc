---
version: 1.0.0
updated: 2026-10-23
---

# Client onboarding checklist

DRAFT. This is a template, not legal advice. A Georgia-licensed attorney reviews it before any client sees it.

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

**Every client:**

- [ ] Red-flag check done; restricted items set aside
- [ ] Heavy, oversized or fragile items marked on Exhibit A
- [ ] Copy of everything signed given to the client

**Red flags:** fresh retail tags or many of the same item, removed or filed-off serial numbers, can't explain where items came from, pressure for quick cash, liens or loans on the items. Stop and ask before going ahead. Heirs with no letters or court order: see the note on [Exhibit B](exhibits.md#exhibit-b-certification-of-authority).

## Office use: where things are recorded

Commands for each step are in [RUN.md, "Consignment (`consign`)"](../../RUN.md).

- **Client name and contact:** only in `inventory/_consign/clients.csv`. Never in git, a PR, an issue or a brief. Everywhere else, use the K## code.
- **Consignment items:** imported into `inventory/CONSIGN-K##/client_ledger.csv` with no spend.
- **Purchases:** `python -m lib.cli context <folder> --spend <amount>`. Record the lot price once.
- **Buyouts:** `python -m lib.cli context <item folder> --spend <buyout_price> --spend-unit item` (our cost basis), and set its row to `EXCLUDED` with the same `buyout_price` in `inventory/CONSIGN-K##/client_ledger.csv` (what we owe the client). These are two sides of one purchase, not two costs: the accountant books one inventory purchase at `buyout_price` when Russell pays, and nothing to "Due to consignors".
- **Agreement and W-9 flags:** once the agreement is signed and the W-9 is in, set `agreement_signed` (the date) and `w9_on_file` (`yes`) for the client in `inventory/_consign/clients.csv`. Never record the TIN.
- **Terms in `clients.csv`**, to match the consignment agreement: `split` 0.5 (section 4.3), `loss_rule` `client_bears_loss` (section 4.5: a below-zero sale is charged back in full), `min_payout` 500 (section 5.3), `listing_days` 180 and `pickup_days` 30 (section 9.1). At termination or the end of the term, `consign settle --final` pays the full balance, even a negative one (section 14.3). These fields, and the per-item ones below, come from the Set B code PR (branch `claude/consign-set-b-terms`). Don't add them before it merges: `consign` on main rejects unknown columns and loss rules.
- **Per item in `client_ledger.csv`:** `floor` from Exhibit A (the client's price protection, optional); `channel` for a sale outside eBay, with its money typed by hand, plus `funds_received` on the day its money reaches us (it clears at the end of the month it sold once that is typed, agreement §5.1(b)); for a booth sale `channel` `mall` and `card_paid` (the part paid by card, 0.00 for cash); `return_notice_sent`, typed by hand on the date the unsold-item notice goes out.
- **Signed paper, W-9s and the ID check:**
  - Signed originals and W-9s stay on paper in a locked file box, one folder per K## code.
  - Never scan or photograph a W-9 or an ID. Don't shoot paperwork with the item cameras: camera imports land in `inventory/`.
  - You may scan signed agreements (never W-9s or IDs) into `~/fairhand-records/K##/`. That folder is outside the repo, `inventory/` and `reports/`, so it is never backed up offsite or read by a session.
  - Record the ID check on this paper checklist only: ID type, date and initials.
  - The files hold only the `agreement_signed` date and the `w9_on_file` yes/no.
- **Purchase packet** (date, amount, payment method with last 4 only, receipt) goes to the accountant, for direct purchases and buyouts alike.
