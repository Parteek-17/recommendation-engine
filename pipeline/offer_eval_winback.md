# Offer evaluation (`python -m pipeline.evaluate_offers`)

Snapshot at **2026-09-30T19:00:00** (end of the synthetic data). Triggers found: win_back 1,029. Evaluated a random sample of **400** (festival excluded: no festival window then).

## Strategies (same guardrail-safe candidates, planted response model)

| Strategy | Offers sent | Redemptions / 1,000 triggers | Incremental / 1,000 | Discount cost / 1,000 (₹) | ₹ per incremental redemption |
|---|---|---|---|---|---|
| engine | 208 | 21.19 | 16.95 | 13,170 | 777.0 |
| random | 208 | 21.19 | 16.95 | 13,170 | 777.0 |
| max_discount | 208 | 21.19 | 16.95 | 13,170 | 777.0 |
| generic | 9 | 1.01 | 0.81 | 10,024 | 12371.0 |

## By trigger (redemptions / 1,000 · ₹ per incremental redemption)

| Trigger | engine | random | max_discount | generic |
|---|---|---|---|---|
| win_back | 21.19 · ₹777.0 | 21.19 · ₹777.0 | 21.19 · ₹777.0 | 1.01 · ₹12371.0 |

## Guardrails

1,600 ranked candidate offers in, **208 kept** (13.0%). Removed, by reason:

| Reason | Offers removed |
|---|---|
| would stack with | 1,254 |
| offer already sent to this user | 123 |
| high-value offer waiting for admin approval | 14 |
| user already used this offer 1x (max 1) | 1 |

## Pipeline funnel (one dry-run scheduler round)

0 random opted-in users linked to a fake Telegram chat.

| Final status | Runs |
|---|---|
