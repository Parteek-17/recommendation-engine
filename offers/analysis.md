# Offer uplift analysis

_Generated 2026-10-23T08:58:58 by `python -m offers.analysis` from `offer_sends` (9 delivered sends)._

**Question:** do bigger discounts make people buy who otherwise wouldn't — or are we paying people who'd buy anyway?

- *Redemption rate* = redeemed ÷ delivered (sent / opened / clicked / redeemed).
- *Organic 7-day rate* = chance the same users order in any 7-day window without an offer.
- *Uplift ≈ redemption − organic.* Near zero or negative ⇒ the discount mostly rewards a purchase that would have happened anyway.
- Price sensitivity = behavioural budget band from the rec engine (low budget ⇒ high sensitivity).

## Redemption rate (sends) by discount size × price sensitivity

| Price sensitivity | small (<10%) | medium (10–20%) | large (>20%) | Organic 7-day rate |
|---|---|---|---|---|
| high | 0% (1) | 0% (1) | – | 12% |
| medium | 0% (2) | 33% (3) | 0% (2) | 8% |

Cells show `rate (number of sends)`.

## Findings

- **high sensitivity:** redemption (0%) is not above the organic 7-day rate (12%) — no measurable uplift; offers here mostly reward purchases that would happen anyway.
- **medium sensitivity:** large discounts redeem 0% vs 0% for small ones — the extra discount buys almost nothing. Keep discounts small. _(too few sends to be sure)_

## What the offer engine does about it

- Discount = base % per trigger × sensitivity multiplier: **1.5× for price-sensitive, 1.0× medium, 0.7× for low-sensitivity** users (`offers/rules.py`).
- Never above `MAX_DISCOUNT_PCT` and never below a product's margin floor, whoever the user is.
- Re-run this analysis when more history exists; if a group's uplift stays near zero, lower its multiplier.

> ⚠️ Small sample: with only a few dozen sends these numbers are anecdotes, not evidence. The method is what matters; re-run on the full synthetic dataset (`DATA_SOURCE=synthetic`).
