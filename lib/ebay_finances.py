#!/usr/bin/env python3
"""ebay_finances — /sell/finances/v1/transaction reader (#119, route B).

`totalMarketplaceFee` on a Fulfillment-API order is the FINAL VALUE FEE ONLY
(#115/#120) — promoted-listing (ad) fees are billed separately and never
appear in that payload, and neither does actual postage. Both DO show up in
the Finances API's transaction feed:

  * `NON_SALE_CHARGE` transactions carry fees billed apart from the sale
    itself — promoted-listing spend among them.
  * `SHIPPING_LABEL` transactions carry what was actually paid for a label,
    keyed to the order it shipped.

This module reads that feed and turns it into two PII-free, order-keyed
figures — sync_actuals.py merges them into `sales_ledger.csv`
(`ad_fee`, `actual_postage`) so the dashboard can drop the "before ads &
postage" qualifier once real numbers are available.

Requires the `sell.finances` scope (added to `USER_SCOPES_SELL` for #119).
Adding the scope does not itself grant access — it only changes what the
NEXT eBay OAuth consent screen asks for. Until the account owner re-runs the
consent flow, every call here fails with 401/403 against the already-issued
refresh_token; that is expected, not a bug in this reader, and callers
(sync_actuals.py) are expected to catch `EbayAuthError` / `EbayAPIError` and
degrade — not crash a whole sync over a scope that isn't consented yet.

----- Traps this module exists to not re-trip (#119) -----

* **Ad cost != ad attribution.** Priority ads are cost-per-click: a listing
  accrues a fee whether or not the resulting sale is flagged
  `soldViaAdCampaign`. `attribute_fees_by_order()` sums ad-fee transactions
  by `orderId` (and `attribute_fees_by_sku()` by SKU) — it never joins on,
  or even looks at, whether a specific sale was ad-attributed. That join is
  `tools/sales_report.py`'s promoted-listings panel's job, from the
  Marketing API side, and stays there.
* **Fee-type is not reliably one field.** eBay's own docs are inconsistent
  about whether/where a `feeType` lands on a `NON_SALE_CHARGE` transaction
  across API versions. `_is_ad_fee()` checks a `feeType`-shaped field AND
  falls back to `transactionMemo` text, and anything that doesn't match a
  known ad-fee marker is filed under `fee_type="OTHER"` (visible in
  `other_fee_labels`) rather than silently counted as ad spend or dropped.
* **Refunds are not zero.** A fully-refunded order can be excluded entirely
  from `sales_ledger.csv` (see `sync_actuals.flatten_orders`'s `excluded`
  set) while still having cost real money — a label bought, an ad fee only
  partly credited. `unwound_order_losses()` takes the set of order ids that
  were dropped as revenue and reports any of them that still carry a fee or
  postage debit here, so that sunk cost surfaces as a loss instead of just
  disappearing along with the (correctly) zeroed-out sale.
* **"Bought" vs "sold" postage are both correct, and different.** A
  transaction's `transactionDate` is when eBay charged for the label, not
  when the item sold — a label bought late one month for an order that sold
  early the next will differ from "postage for orders sold this month" by a
  few dollars, and that is not a bug in either number. This module exposes
  BOTH: `attribute_postage_by_order()` is the "sold" side (postage matched
  to a specific order id, however far its label purchase date sits from the
  order's sale date); `total_postage_bought()` is the "bought" side (every
  `SHIPPING_LABEL` transaction in the window, unmatched). `sync_actuals.py`
  writes the "sold" figure into `sales_ledger.csv` per order and its report
  names it explicitly as such — see that module for where the "bought"
  total is surfaced instead, so the two are never presented as one number.

----- PII -----

A raw Finances-API transaction can carry `buyerInfo` (buyer username) and
other order-linkable fields. Nothing this module returns carries a raw
transaction dict, `buyerInfo`, or verbatim `transactionMemo` text — every
returned record is built field-by-field from an explicit allowlist (order
id, SKU, a same-file-classified fee_type label, a Decimal amount, a
Pacific-bucketed date string; see `FeeLine` / `PostageLine`). `feeType`/
`transactionMemo` are read ONLY to classify a fee as ad-vs-other and are
never copied into a returned record or logged.

----- Sign convention -----

`FeeLine.amount` / `PostageLine.amount` and the `attribute_*` dicts keep
eBay's own sign (a charge is negative — a payout-reducing transaction) since
that is what a caller summing several transactions together needs to net
correctly. `unwound_order_losses()` is the one exception: it returns
POSITIVE magnitudes (money actually lost), matching the existing
`sales_ledger.csv` convention (`ebay_fee`, `item_price`, ... are all stored
positive) — `sync_actuals.py` does the same `abs()` when it merges
`attribute_fees_by_order` / `attribute_postage_by_order` into a sales row's
`ad_fee` / `actual_postage` columns, for the same reason.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ebay_client import api_send  # noqa: E402
from report import to_report_date  # noqa: E402  Pacific bucketing (#122 convention)

# transactionType values that carry a real dollar amount against an order.
# Built defensively: any transactionType this module doesn't recognize
# (SALE, REFUND, CREDIT, TRANSFER, ADJUSTMENT, DISPUTE, ...) is simply
# skipped rather than mis-parsed — this reader only claims to extract fees
# and postage, nothing else.
FEE_TRANSACTION_TYPE = "NON_SALE_CHARGE"
POSTAGE_TRANSACTION_TYPE = "SHIPPING_LABEL"

# Case-insensitive substrings that flag a NON_SALE_CHARGE as promoted-listing
# / ad spend specifically, checked against both a feeType-shaped field (if
# present) and transactionMemo — see module docstring "Fee-type is not
# reliably one field".
_AD_FEE_MARKERS = ("ad fee", "ad_fee", "promoted", "promotion", "advertising")


@dataclass(frozen=True)
class FeeLine:
    """One NON_SALE_CHARGE, PII-free. `amount` is signed as eBay reports it
    (a fee is negative)."""
    order_id: str
    sku: str
    fee_type: str   # "AD" or "OTHER" — never eBay's raw feeType/memo text
    amount: Decimal
    date: str       # YYYY-MM-DD, Pacific-bucketed (report.to_report_date); "" if unparsable


@dataclass(frozen=True)
class PostageLine:
    """One SHIPPING_LABEL transaction, PII-free."""
    order_id: str
    amount: Decimal
    date: str


# ---------------------------------------------------------------------------
# money / field helpers
# ---------------------------------------------------------------------------

def _dec(v) -> Decimal:
    if isinstance(v, dict):
        v = v.get("value")
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return Decimal(0)


def _is_ad_fee(txn: dict) -> bool:
    label = " ".join(str(txn.get(k) or "") for k in ("feeType", "transactionMemo")).lower()
    return any(m in label for m in _AD_FEE_MARKERS)


def _other_fee_label(txn: dict) -> str:
    """A short, PII-free label for a non-ad fee, for `other_fee_labels` —
    the raw feeType if eBay sent one, else a fixed placeholder. Never the
    memo text (which is free-form and not vetted PII-free)."""
    return (txn.get("feeType") or "").strip() or "(unlabeled NON_SALE_CHARGE)"


def _skus_for(txn: dict) -> list[str]:
    items = txn.get("orderLineItems") or []
    skus = [li.get("sku") for li in items if li.get("sku")]
    return skus or [""]


# ---------------------------------------------------------------------------
# fetch — paginated, windowed like sync_actuals.fetch_orders
# ---------------------------------------------------------------------------

def _fetch_transactions_window(start: datetime, end: datetime, verbose: bool) -> list[dict]:
    # %5B/%5D, not raw [/]: matches the known-working pattern in
    # sync_actuals.fetch_orders (raw brackets in the query string can cause
    # the request to be rejected or parsed inconsistently).
    date_range = (f"transactionDate:%5B{start.strftime('%Y-%m-%dT%H:%M:%S.000Z')}.."
                  f"{end.strftime('%Y-%m-%dT%H:%M:%S.000Z')}%5D")
    out: list[dict] = []
    offset, limit = 0, 200
    while True:
        path = (f"/sell/finances/v1/transaction?limit={limit}&offset={offset}"
                f"&filter={date_range}")
        data = api_send("GET", path, creds=None, marketplace=None)
        batch = data.get("transactions") or []
        out.extend(batch)
        total = data.get("total")
        if verbose:
            shown_total = total if total is not None else len(out)
            print(f"  transactions {len(out)}/{shown_total}", end="\r")
        # Advance by the batch actually returned, not the limit requested —
        # a server-side page cap smaller than `limit` would otherwise skip
        # transactions. Only stop on a genuinely EMPTY page: a short page
        # (len(batch) < limit) is NOT a reliable end-of-results signal on
        # its own — if the server enforces a page cap smaller than `limit`
        # on every page, every page would look "short" and this would stop
        # after page one. `total`, when present, still short-circuits the
        # common case rather than always paying for one trailing empty call.
        offset += len(batch)
        if not batch or (total is not None and offset >= total):
            break
    return out


def fetch_transactions(days: int, verbose: bool = True) -> list[dict]:
    """Every Finances-API transaction in the last `days`, paged.

    Mirrors `sync_actuals.fetch_orders`: eBay's date-range filters can refuse
    a window that reaches too far back, and the exact cutoff for THIS
    endpoint is undocumented here (unverified against a live account — no
    credentials in this environment) — so a 400 narrows the window the same
    way, rather than hard-coding a number nobody has confirmed for
    /sell/finances/v1/transaction specifically.

    Raises whatever `EbayAuthError`/`EbayAPIError` the underlying call
    raises (in particular 401/403 before the account owner has re-consented
    with `sell.finances` — see module docstring). Callers should catch and
    degrade, not let this take down a whole sync.
    """
    end = datetime.now(timezone.utc)
    # dict.fromkeys, not a plain list comp: when `days` itself equals one of
    # the hard-coded candidates (365, 180, ...), the naive filter would list
    # that window twice and retry the identical rejected request before
    # actually narrowing anything.
    windows = list(dict.fromkeys(d for d in (days, 540, 365, 180, 90) if d <= days)) or [days]
    last_err: Optional[Exception] = None
    for attempt in windows:
        start = end - timedelta(days=attempt)
        try:
            txns = _fetch_transactions_window(start, end, verbose)
        except Exception as e:                                  # noqa: BLE001
            if "400" not in str(e):
                raise
            last_err = e
            if verbose:
                print(f"  eBay refused a {attempt}-day window; narrowing…" + " " * 20)
            continue
        if verbose:
            note = "" if attempt == days else f"  (requested {days}; eBay capped it)"
            print(f"  transactions: {len(txns)} in the last {attempt} days{note}" + " " * 12)
        return txns
    raise RuntimeError(
        f"eBay rejected every transaction window tried ({', '.join(str(w) for w in windows)} "
        f"days). Last error: {last_err}") from last_err


# ---------------------------------------------------------------------------
# parse — raw transactions -> PII-free FeeLine / PostageLine
# ---------------------------------------------------------------------------

def parse_transactions(transactions: list[dict]) -> dict:
    """Split raw Finances-API transactions into fee lines and postage lines.

    Returns:
        {"fees": [FeeLine, ...], "postage": [PostageLine, ...],
         "other_fee_labels": Counter}

    `other_fee_labels` counts every NON_SALE_CHARGE that did NOT match an
    ad-fee marker, by its raw feeType (or a placeholder) — visible so an
    unrecognized fee category shows up instead of silently being counted as
    neither ad spend nor anything else. It is a label/count Counter, not a
    dollar figure, and carries no order-linkable data.

    A single fee transaction that covers a multi-SKU order is split evenly
    across the order's line-item SKUs (eBay does not itemize a
    NON_SALE_CHARGE per line) rather than attributed whole to one SKU.
    """
    fees: list[FeeLine] = []
    postage: list[PostageLine] = []
    other_labels: Counter = Counter()

    for t in transactions or []:
        ttype = t.get("transactionType") or ""
        order_id = t.get("orderId") or ""
        d = to_report_date(t.get("transactionDate") or "")
        date_s = d.isoformat() if d else ""

        if ttype == FEE_TRANSACTION_TYPE:
            amt = _dec(t.get("amount"))
            is_ad = _is_ad_fee(t)
            if not is_ad:
                other_labels[_other_fee_label(t)] += 1
            fee_type = "AD" if is_ad else "OTHER"
            skus = _skus_for(t)
            # Quantized per-SKU, with the rounding remainder folded into the
            # last SKU, so the shares sum back to `amt` exactly (a plain
            # amt / len(skus) can drift for amounts that don't divide evenly,
            # e.g. $1.00 across 3 SKUs).
            if len(skus) > 1:
                share = (amt / len(skus)).quantize(Decimal("0.01"))
                shares = [share] * len(skus)
                shares[-1] += amt - sum(shares, Decimal(0))
            else:
                shares = [amt]
            for sku, s in zip(skus, shares):
                fees.append(FeeLine(order_id=order_id, sku=sku, fee_type=fee_type,
                                    amount=s, date=date_s))
        elif ttype == POSTAGE_TRANSACTION_TYPE:
            postage.append(PostageLine(order_id=order_id, amount=_dec(t.get("amount")),
                                       date=date_s))
        # everything else (SALE, REFUND, CREDIT, TRANSFER, ADJUSTMENT,
        # DISPUTE, ...) is out of scope for this reader and skipped.

    return {"fees": fees, "postage": postage, "other_fee_labels": other_labels}


# ---------------------------------------------------------------------------
# attribution — by order / by SKU, never by soldViaAdCampaign
# ---------------------------------------------------------------------------

def attribute_fees_by_order(fees: list[FeeLine], *, ad_only: bool = True) -> dict[str, Decimal]:
    """{order_id: total fee amount}, summed independent of whether any sale
    on that order was flagged `soldViaAdCampaign` — cost-per-click ads bill
    whether or not eBay credits the sale to the campaign (#119 trap: "ad
    cost != ad attribution"). `ad_only=True` (default) sums only fees
    classified AD; pass False to include OTHER non-sale charges too."""
    out: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for f in fees:
        if not f.order_id:
            continue
        if ad_only and f.fee_type != "AD":
            continue
        out[f.order_id] += f.amount
    return dict(out)


def attribute_fees_by_sku(fees: list[FeeLine], *, ad_only: bool = True) -> dict[str, Decimal]:
    """{sku: total fee amount} — same independence from soldViaAdCampaign as
    `attribute_fees_by_order`, keyed by SKU instead for callers that match
    sales by SKU rather than order id (matches `sync_actuals.match_sale`'s
    SKU-first convention)."""
    out: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for f in fees:
        if not f.sku:
            continue
        if ad_only and f.fee_type != "AD":
            continue
        out[f.sku] += f.amount
    return dict(out)


def attribute_postage_by_order(postage: list[PostageLine]) -> dict[str, Decimal]:
    """{order_id: total label cost} — the "sold" side postage figure: every
    SHIPPING_LABEL transaction matched to the order it shipped, regardless
    of which reporting period the label purchase itself falls in. See
    `total_postage_bought` for the other, "bought in period", figure."""
    out: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for p in postage:
        if not p.order_id:
            continue
        out[p.order_id] += p.amount
    return dict(out)


def total_postage_bought(postage: list[PostageLine]) -> Decimal:
    """Sum of every SHIPPING_LABEL transaction in the window, unmatched to
    any order — the "bought in period" figure, distinct from
    `attribute_postage_by_order`'s "sold in period" figure by a few percent
    on any real account (labels bought for orders outside the window, or
    vice versa). Never present both as if they were the same number."""
    return sum((p.amount for p in postage), Decimal(0))


def unwound_order_losses(excluded_order_ids: list[str],
                         ad_fee_by_order: dict[str, Decimal],
                         postage_by_order: dict[str, Decimal]) -> list[dict]:
    """Orders dropped from `sales_ledger.csv` as fully-refunded (zero/negative
    `totalDueSeller` — see `sync_actuals.flatten_orders`) that still carry a
    real ad-fee or postage debit here. #119 trap: "refunds are not zero" — a
    label bought and an ad fee only partly credited back are real money lost
    even though the sale correctly shows no revenue. Returned once per
    order, PII-free (order id + amounts only), never silently dropped.

    Amounts are POSITIVE magnitudes (money lost) — see module docstring
    "Sign convention" — unlike `ad_fee_by_order`/`postage_by_order`'s own
    eBay-signed values."""
    out = []
    seen = set()
    for oid in excluded_order_ids:
        if not oid or oid in seen:
            continue
        seen.add(oid)
        fee = abs(ad_fee_by_order.get(oid, Decimal(0)))
        post = abs(postage_by_order.get(oid, Decimal(0)))
        if fee or post:
            out.append({"order_id": oid, "ad_fee": fee, "actual_postage": post,
                       "loss": fee + post})
    return out
