---
version: 1.0.0
updated: 2026-10-23
---

# Consignment Agreement

DRAFT. This is a template, not legal advice. A Georgia-licensed attorney reviews it before any client sees it.

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

2.1. We may sell your items through eBay or any other channel we choose. eBay includes our eBay store(s) and eBay Live. Other channels include other online marketplaces, furniture or other dealers, and our antique-mall booth.

2.2. We choose the channel for each item, and we may move an item from one channel to another. Wherever we sell it, the same terms apply, including any floor you set (section 3.2). <!-- Internal: sales tax on non-eBay sales of consigned items is still open (GH #281). Not client wording. -->

2.3. We photograph, describe and list the items, and pack, ship or deliver them when they sell. We describe condition honestly, including the defects you tell us about.

## 3. Prices

3.1. We choose where and how to sell each item: the channel, the listing format, the price and any markdowns. We do this within any floor you set on Exhibit A. <!-- TODO(attorney): does selling a consigned item by auction (eBay auction format, or an auction on eBay Live) need a Georgia auctioneer license? Until answered, tools/consign.py flags any auction-format listing of a consigned item. Not client wording. -->

3.2. You may set a lowest price (a "floor") for any item in the `floor` column of Exhibit A. The floor is the lowest price you'll accept for the item, before any shipping. It applies wherever we sell the item, and we won't sell it for less without your written OK. Floors are your price protection: they are how you control the lowest price an item can sell for.

## 4. The money

4.1. **Gross Proceeds** for an eBay sale means the item price plus the shipping the buyer paid, less any refund. Sales tax that eBay collects is not part of Gross Proceeds.

4.2. **Net Proceeds** means:
(a) for an eBay sale, eBay's payout for the sale before postage (Gross Proceeds less eBay's fees), minus (1) promoted-listing (advertising) fees for the item, (2) the actual postage to ship it, and (3) any other cost listed on Exhibit A or that you approve in writing;
(b) for a sale anywhere else we choose (another marketplace, a furniture or other dealer, or our antique-mall booth), the sale price we actually receive, less any refund, minus (1) that channel's commission or fees, unless they were already taken out before we were paid, (2) transport or delivery, and (3) any other direct cost listed on Exhibit A or that you approve in writing.
<!-- Internal: a non-eBay sale is a `client_ledger.csv` row with `channel` set and its money typed by hand (the Set B code PR (branch claude/consign-set-b-terms)). Sales tax on non-eBay sales is not decided here (GH #281). Not client wording. -->

4.3. **Your share** is 50% of Net Proceeds. We keep the rest.

4.4. A cost we don't know yet is not zero. An item isn't Cleared until every cost for it is known, wherever it sold.

4.5. If a sale's Net Proceeds come out below zero, we don't take that loss. Section 4.3 doesn't apply to that sale: your share is the whole shortfall (a negative amount), and we take it from your next payouts or you repay it as in section 8.1. We expect this to be rare, but we can't rule it out. The floors you set on Exhibit A are your protection against it (section 3.2). <!-- Internal: Russell's final answer (PR #286 comment, 2026-10-23): a below-zero sale is charged back to the client in full. tools/consign.py has no loss_rule for this yet (`split` charges only the client's split of a loss, `client_never_owes` charges nothing). Set `loss_rule` to `client_bears_loss` in clients.csv (the Set B code PR (branch claude/consign-set-b-terms)); until that merges, `split` under-charges a loss by our half. Not client wording. -->

## 5. When you get paid

5.1. An item is **Cleared** once every cost for it is known and:
(a) for an eBay sale, at the later of 40 days after it sells or 30 days after the buyer receives it. eBay's 30-day return window runs from delivery, so this gives returns time to come in;
(b) for any other sale, ____ days after we receive the funds (recommended: 30 days after we receive the funds), so a bounced payment, chargeback or dealer return has time to show up.
We owe you your share once an item is Cleared, not when it sells. <!-- Internal: 40 must match RETURN_WINDOW_DAYS and 30 must match DELIVERY_WINDOW_DAYS in tools/consign.py (#285); change the agreement and the code together. --> <!-- TODO(Russell): set the non-eBay clearing period. Internal: the Set B code clears a `channel` sale at sale + 40 days (no delivery date), so 40 days after the sale matches the code; any other number needs a code change too. 30 days matches the eBay return window and gives a bounced check, a card chargeback notice or a dealer's return time to arrive before we pay out. -->

5.2. We pay monthly. Items Cleared by the last day of a month are paid by the 15th of the next month.

5.3. If your balance on a payout date is under $500, we carry it forward to the next payout. When this agreement ends, whether either of us ends it or its term runs out, the $500 minimum no longer applies: we pay the full balance owed in the next payout run, whatever the amount (see section 14.3). <!-- Internal: `min_payout` 500 in clients.csv; `consign settle --final` pays the full balance at the end (Set B code PR). Not client wording. -->

5.4. Each payout comes with a statement listing each item sold, its sale date, Gross Proceeds, each cost, Net Proceeds and your share.

## 6. How you get paid

6.1. We pay you by (tick one): [ ] Check [ ] ACH bank transfer

6.2. Before your first payout, you give us a signed IRS Form W-9. We can't pay you until we have it. <!-- Internal CPA question: whether consignment payouts need a 1099 from us. Not client wording. -->

## 7. Buyouts

7.1. We may offer to buy an item from you outright at a stated buyout price. A buyout only happens if you agree in writing (a signed note or an email reply is enough), naming the item and the price.

7.2. We pay the buyout price in full, with no split, __________ (recommended: in the next payout run after you agree). The item is ours from the date you agree to the buyout, and we owe you the buyout price until we pay it. Post-sale costs and refunds on a bought-out item are ours, not yours. <!-- TODO(Russell): set when buyouts are paid. TODO(attorney): confirm title passes on agreement for a buyout (the purchase agreement passes title on payment). -->

## 8. Returns and claims after payout

8.1. If a buyer returns an item, files a chargeback or wins an eBay Money Back Guarantee claim after we have paid you for it, we recalculate your share for that sale and take the difference out of your next payout. If there is no next payout, or it doesn't cover the difference, you repay the rest within 30 days of our written notice. A shortfall under section 4.5 is taken from your payouts and repaid the same way.

8.2. A returned item goes back on sale under this agreement.

## 9. Unsold items

9.1. If an item hasn't sold after 180 days listed, we tell you. You then have 30 days to pick it up or agree to a markdown. <!-- Internal: `listing_days` 180 and `pickup_days` 30 in clients.csv; type the date into `return_notice_sent` on the item's row when the notice goes out (Set B code PR). Not client wording. -->

9.2. If you don't pick it up or answer in that time, we may donate or dispose of the item, and you owe us nothing for it. <!-- TODO(attorney): confirm Georgia abandoned-property wording and notice requirements. -->

## 10. Storage, care and risk of loss

10.1. We charge no storage fee during this agreement or the pickup period.

10.2. We take reasonable care of your items. We do not insure them unless we agree in writing. You should keep your own insurance on them. Our liability for loss or damage while items are in our care is limited to __________. <!-- TODO(Russell/attorney): set the liability limit and confirm the insurance wording. -->

## 11. Shipping

We pack and ship every sale that needs shipping. Postage, or transport or delivery for a sale outside eBay, comes out of Net Proceeds (section 4.2).

## 12. Your promises

You promise that:
(a) you own the items, or have the authority to sell them (see Exhibit B if you sign for a trust, estate, another person or a company);
(b) no one else has a lien, loan or claim on them;
(c) they are not stolen;
(d) to your knowledge, none is counterfeit; and
(e) you have told us about every defect you know of.

If any of these turns out to be untrue, we may return the items, and you cover any refund we have to give a buyer because of it.

## 13. Items we don't accept

We don't take anything the channel we sell through doesn't allow. On eBay that includes firearms or firearm parts, and ivory or other animal parts. <!-- Internal: precious metals/jewelry dealer registration is still open (GH #281); don't decide here. -->

## 14. Term and ending

14.1. This agreement runs for __________ from the date signed. <!-- TODO(Russell): set the term. -->

14.2. Either of us may end it with ____ days' written notice. <!-- TODO(Russell): set the notice period. -->

14.3. Items listed when it ends may finish their current listing. We pay every Cleared balance, and any buyout price owed, in the next payout run, whatever the amount (the minimum in section 5.3 doesn't apply). Items that clear after that are paid in the payout run after they clear, also with no minimum. Unsold items come back to you under section 9.

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

### Russell Fair Enterprises, LLC, a Georgia limited liability company, d/b/a The Fair Hand

By (signature): __________ Name: __________ Title: __________ Date: __________

Attached: Exhibit A (item schedule), ____ sheet(s). Exhibit B (certification of authority), if signing for someone else. See [exhibits.md](exhibits.md).
