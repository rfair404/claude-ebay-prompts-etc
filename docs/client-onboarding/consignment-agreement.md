---
version: 1.0.0
updated: 2026-10-23
---

# Consignment Agreement

DRAFT. This is a template, not legal advice. Have it reviewed by a Georgia-licensed attorney before use.

<!-- TODO(Russell): confirm legal name, state of formation and DBA on client paperwork. -->

**Us:** Russell Fair Enterprises, LLC, a Georgia limited liability company, d/b/a The Fair Hand ("we", "us").
Address: __________ Phone: __________ Email: __________

**You (the Consignor):** __________ (your name, or the legal name of the trust, estate or company you sign for)
Address: __________ Phone: __________ Email: __________
Client code: K____ Date signed: __________

You want us to sell some of your items for you. You keep owning them until they sell. This agreement says how we sell them, how the money is split, and when you get paid. The items are listed on **Exhibit A** (item schedule), which is part of this agreement.

## 1. The items

1.1. You give us the items on Exhibit A to sell for you. We both initial each Exhibit A sheet.

1.2. At intake we may tell you an item isn't one we can sell well. We give those back to you, and they are marked RETURN on Exhibit A.

1.3. Items you add later are added on a new Exhibit A sheet that we both initial.

## 2. Where and how we sell

2.1. We sell your items through our eBay store(s), including Pop's Games, and eBay Live.

2.2. This agreement covers eBay sales only. We won't sell your items in person or at a booth unless we both agree in writing first. <!-- Internal: in-person/booth sales of consigned items are out until sales-tax handling is settled (GH #281). Not client wording. -->

2.3. We photograph, describe, list, pack and ship the items. We describe condition honestly, including the defects you tell us about.

## 3. Prices

3.1. We set prices, choose the format (fixed price or auction) and decide markdowns.

3.2. You may set a lowest price (a "floor") for any item in the `floor` column of Exhibit A. The floor is the item price, not counting shipping. We won't sell that item for less without your written OK.

## 4. The money

4.1. **Gross Proceeds** means the item price plus the shipping the buyer paid, less any refund. Sales tax that eBay collects is not part of Gross Proceeds.

4.2. **Net Proceeds** means eBay's payout for the sale before postage (Gross Proceeds less eBay's fees), minus:
(a) promoted-listing (advertising) fees for the item;
(b) the actual postage to ship it; and
(c) any other cost listed on Exhibit A or that you approve in writing.

4.3. **Your share** is ____% of Net Proceeds (recommended: 50%). We keep the rest. <!-- TODO(Russell): set the consignor split. -->

4.4. A cost we don't know yet is not zero. An item isn't Cleared until every cost for it is known.

4.5. If a sale's Net Proceeds come out below zero (tick one; we both initial): [ ] you share the loss at your split, and it is taken from your next payout as in section 8.1 [ ] you never owe; your share of that sale is $0, including when we recalculate it under section 8.1
(Recommended: you never owe.) Initials: You ____ Us ____ <!-- Internal: recommend client_never_owes because we set the price, format, ads and postage, so a below-zero sale is our call, and the amounts are too small to be worth collecting from a client. The ticked box is the loss_rule in clients.csv (split / client_never_owes). TODO(Russell): confirm the recommended option. -->

## 5. When you get paid

5.1. An item is **Cleared** at the later of ____ days after it sells (recommended: 40) or ____ days after the buyer receives it (recommended: 30), with all costs known. eBay's 30-day return window runs from delivery, so this gives returns time to come in. We owe you your share once an item is Cleared, not when it sells. <!-- TODO(Russell): set both clearing periods. 40 must match RETURN_WINDOW_DAYS and 30 must match DELIVERY_WINDOW_DAYS in tools/consign.py; change the agreement and the code together. -->

5.2. Payout schedule: __________ (recommended: monthly. Items Cleared by the last day of a month are paid by the 15th of the next month.) <!-- TODO(Russell): set the payout schedule. -->

5.3. If your balance is under $____ (recommended: $25), we carry it forward to the next payout. When this agreement ends, we pay everything owed, whatever the amount. <!-- TODO(Russell): set the minimum payout. -->

5.4. Each payout comes with a statement listing each item sold, its sale date, Gross Proceeds, each cost, Net Proceeds and your share.

## 6. How you get paid

6.1. Payout method (tick one): [ ] Check [ ] ACH bank transfer [ ] Zelle or other payment app: __________ [ ] Cash, with a receipt you sign
(Recommended: check or ACH.) <!-- TODO(Russell): confirm which payout methods we offer. -->

6.2. Before your first payout, you give us a signed IRS Form W-9. We can't pay you until we have it. <!-- TODO(Russell): confirm W-9 before first payout (recommended). Internal CPA question: whether consignment payouts need a 1099 from us. Not client wording. -->

## 7. Buyouts

7.1. We may offer to buy an item from you outright at a stated buyout price. A buyout only happens if you agree in writing (a signed note or an email reply is enough), naming the item and the price.

7.2. We pay the buyout price in full, with no split, __________ (recommended: in the next payout run after you agree). The item is ours from the date you agree to the buyout, and we owe you the buyout price until we pay it. Post-sale costs and refunds on a bought-out item are ours, not yours. <!-- TODO(Russell): set when buyouts are paid. TODO(attorney): confirm title passes on agreement for a buyout (the purchase agreement passes title on payment). -->

## 8. Returns and claims after payout

8.1. If a buyer returns an item, files a chargeback or wins an eBay Money Back Guarantee claim after we have paid you for it, we recalculate your share for that sale and take the difference out of your next payout. If there is no next payout, you repay it within ____ days (recommended: 30) of our written notice. <!-- TODO(Russell): confirm the offset and set the repayment period. -->

8.2. A returned item goes back on sale under this agreement.

## 9. Unsold items

9.1. If an item hasn't sold after ____ days listed (recommended: 90), we tell you. You then have ____ days (recommended: 30) to pick it up or agree to a markdown. <!-- TODO(Russell): set the unsold-notice and pickup periods. -->

9.2. If you don't pick it up or answer in that time, we may donate or dispose of the item, and you owe us nothing for it. <!-- TODO(attorney): confirm Georgia abandoned-property wording and notice requirements. -->

## 10. Storage, care and risk of loss

10.1. We charge no storage fee during this agreement or the pickup period. <!-- TODO(Russell): confirm no storage fee (recommended default). -->

10.2. We take reasonable care of your items. We do not insure them unless we agree in writing. You should keep your own insurance on them. Our liability for loss or damage while items are in our care is limited to __________. <!-- TODO(Russell/attorney): set the liability limit and confirm the insurance wording. -->

## 11. Shipping

We pack and ship every sale. Postage comes out of Net Proceeds (section 4.2).

## 12. Your promises

You promise that:
(a) you own the items, or have the authority to sell them (see Exhibit B if you sign for a trust, estate, another person or a company);
(b) no one else has a lien, loan or claim on them;
(c) they are not stolen;
(d) to your knowledge, none is counterfeit; and
(e) you have told us about every defect you know of.

If any of these turns out to be untrue, we may return the items, and you cover any refund we have to give a buyer because of it.

## 13. Items we don't accept

We don't take firearms or firearm parts, ivory or other animal parts, or anything eBay's policies don't allow us to list. <!-- TODO(Russell): confirm the restricted-items list. Internal: precious metals/jewelry dealer registration is still open (GH #281); don't decide here. -->

## 14. Term and ending

14.1. This agreement runs for __________ from the date signed. <!-- TODO(Russell): set the term. -->

14.2. Either of us may end it with ____ days' written notice. <!-- TODO(Russell): set the notice period. -->

14.3. Items listed when it ends may finish their current listing. We pay every Cleared balance, and any buyout price owed, in the next payout run. Unsold items come back to you under section 9.

## 15. Your information

We keep your name, contact details and tax form private. We don't share them, except as needed to send your payment, prepare tax forms or follow the law.

## 16. General

16.1. Georgia law governs this agreement.

16.2. This agreement and its exhibits are our whole deal. Changes must be in writing and agreed by both of us.

16.3. If you sign for a trust, estate, another person or a company, you also sign Exhibit B (certification of authority) and attach the proof it lists.

16.4. You get a copy of everything you sign.

## Signatures

Sign the block that fits you.

### Individual (signing for yourself)

Signature: __________ Printed name: __________ Date: __________

### Trustee or other fiduciary

__________ (printed name), as [ ] trustee of the __________ Trust dated __________ [ ] executor of the Estate of __________ [ ] administrator of the Estate of __________ [ ] agent under a power of attorney for __________

Signature: __________ Date: __________ [ ] Exhibit B signed and proof attached

### Company (authorized signer)

Legal name of company (as on the Secretary of State record): __________
By (signature): __________ Name: __________ Title: __________ Date: __________
[ ] Exhibit B signed and proof attached

### Russell Fair Enterprises, LLC d/b/a The Fair Hand

By (signature): __________ Name: __________ Title: __________ Date: __________

Attached: Exhibit A (item schedule), ____ sheet(s). Exhibit B (certification of authority), if signing for someone else. See [exhibits.md](exhibits.md).
