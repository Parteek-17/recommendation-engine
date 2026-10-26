# Offer evaluation (`python -m pipeline.evaluate_offers`)

Snapshot at **2026-09-30T19:00:00** (end of the synthetic data). Triggers found: cart_abandon 552, post_purchase 588, repurchase_due 532, win_back 1,029. Evaluated a random sample of **1500** (festival excluded: no festival window then).

## Strategies (same guardrail-safe candidates, planted response model)

| Strategy | Offers sent | Redemptions / 1,000 triggers | Incremental / 1,000 | Discount cost / 1,000 (₹) | ₹ per incremental redemption |
|---|---|---|---|---|---|
| engine | 783 | 8.61 | 6.89 | 757 | 110.0 |
| random | 783 | 3.76 | 3.01 | 375 | 124.0 |
| max_discount | 783 | 3.27 | 2.62 | 581 | 222.0 |
| generic | 488 | 3.89 | 3.12 | 345 | 111.0 |

## By trigger (redemptions / 1,000 · ₹ per incremental redemption)

| Trigger | engine | random | max_discount | generic |
|---|---|---|---|---|
| cart_abandon | 16.96 · ₹173.0 | 3.22 · ₹381.0 | 1.33 · ₹1032.0 | 1.05 · ₹896.0 |
| post_purchase | 20.47 · ₹85.0 | 14.4 · ₹76.0 | 13.97 · ₹155.0 | 15.28 · ₹72.0 |
| repurchase_due | 5.05 · ₹17.0 | 0.61 · ₹14.0 | 0.44 · ₹92.0 | 2.36 · ₹36.0 |
| win_back | 0.0 · ₹– | 0.0 · ₹– | 0.0 · ₹– | 0.0 · ₹– |

## Guardrails

12,571 ranked candidate offers in, **3,194 kept** (25.4%). Removed, by reason:

| Reason | Offers removed |
|---|---|
| would stack with | 8,291 |
| offer already sent to this user | 647 |
| discount | 298 |
| high-value offer waiting for admin approval | 116 |
| category with age-restricted products offered to a minor | 16 |
| user already used this offer 1x (max 1) | 9 |

## Pipeline funnel (one dry-run scheduler round)

400 random opted-in users linked to a fake Telegram chat.

| Final status | Runs |
|---|---|
| unreachable | 2,594 |
| guardrail_blocked | 86 |
| dry_run | 13 |
| blocked | 8 |
| cooldown | 0 |

Eligibility blocks: daily cap reached (1/1) (6); weekly cap reached (3/3) (1); weekly cap reached (1/1) (1)
