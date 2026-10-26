# Synthetic Data Parameters

The exact knobs Phase 1 (`data_gen/config.py`) uses to create the dataset. All values are **defaults** — change them in the config, never hard-code them.
Everything is random but **seeded** (`SEED = 42`) → same seed = identical data.

---

## 1. Global

| Parameter | Small | Full |
|---|---|---|
| Users | 1,000 | 10,000 |
| Products | 500 | 3,000 |
| Categories | 8 parents / 25 sub | 8 parents / 25 sub |
| Events (approx.) | ~80k | ~1M |
| Date window | 2026-04-01 → 2026-09-30 | same |
| Seed | 42 | 42 |

## 2. Products (catalog)

| Parameter | Value |
|---|---|
| Parent categories | Electronics, Fashion, Home & Kitchen, Beauty, Grocery, Sports, Books, Toys |
| Brands per sub-category | 5–15 (realistic Indian + global names) |
| Title / description | Templates: `brand + product type + 2–3 attributes` |
| Price range (₹) | Electronics 300–80,000 · Fashion 200–5,000 · Home 150–15,000 · Beauty 100–2,500 · Grocery 30–1,500 · Sports 200–10,000 · Books 99–1,200 · Toys 150–4,000 (log-uniform inside range) |
| MRP | price × (1 + uniform 0–40%) |
| margin_pct | Electronics 5–15% · Grocery 5–12% · Books 10–25% · Home/Sports 20–35% · Beauty/Toys 25–40% · Fashion 30–50% |
| avg_rating | Normal(4.0, 0.4), clipped 1–5 (hidden "quality" drives it) |
| rating_count | Power-law (most products few ratings, bestsellers thousands) |
| Popularity | Zipf exponent ≈ 1.1 → top 20% of products get ~75% of views |
| Out of stock | 5% of products |
| Late launches (cold-start items) | 10% launched in the last 6 weeks |
| Consumables | Grocery + Beauty (+ some Home) → `repurchase_days` 15–60 |
| Age-restricted | ~1–2% of products |

## 3. Product relations (ground truth)

| Parameter | Value |
|---|---|
| Complements | Defined rules, e.g. phone→cover/charger/earphones, laptop→bag/mouse, shoes→socks, camera→memory card. ~1–5 per product where applicable |
| Substitutes | Same sub-category and price within ±30% |

## 4. Users

| Parameter | Distribution |
|---|---|
| age_group | 13–17: 5% (`is_minor=1`) · 18–24: 30% · 25–34: 35% · 35–44: 18% · 45+: 12% |
| gender | F 45% · M 53% · Other 2% (descriptive only — **never used for offers**) |
| city_tier | Tier-1 40% · Tier-2 35% · Tier-3 25% (city names via Faker `en_IN`) |
| device | Mobile 75% · Desktop 25% |
| signup_date | Spread over window; 10% in the last month (cold-start users) |

### Hidden taste profile (ground truth — models never see it)

| Parameter | Value |
|---|---|
| preferred_categories | 2–3 sub-categories per user (Dirichlet weights) |
| price_sensitivity | Beta(2, 2) → 0 = premium buyer, 1 = bargain hunter; shifted +0.1 for Tier-3 |
| activity_level | Light 50% (1–2 sessions/month) · Regular 35% (4–8) · Heavy 15% (15–30) |
| is_gifter | 15% of users |

## 5. Behaviour simulation

| Parameter | Value |
|---|---|
| Events per session | 3–15 |
| Taste vs. exploration | 80% preferred categories · 20% random |
| Sessions with a search | 10% |
| Gift sessions | 5% of a gifter's sessions; browse outside usual categories |
| Abandoned sessions (no cart) | ~30% |
| Noise clicks | 5% random products |

### Purchase funnel (base rates)

| Step | Probability |
|---|---|
| view → wishlist | 5% |
| view → add_to_cart | 10% |
| add_to_cart → remove_from_cart | 15% |
| add_to_cart → purchase | 30% |

### Multipliers on those rates

| Condition | Effect |
|---|---|
| Product in user's preferred categories | × 2.0 |
| Price inside user's comfort range | × 1.5 · outside → × 0.4 |
| High rating (≥ 4.3) | × 1.2 |
| Sale period | traffic × 2, conversion × 1.8 |
| Viral product (10 products, 2–3 weeks) | views × 5 |

## 6. Planted patterns

| Pattern | Parameter |
|---|---|
| Post-purchase: substitutes | Interest × 0.1 for 60 days after buying a non-consumable |
| Post-purchase: complements | Interest × 3 for 14 days; 25% chance to buy one |
| Repurchase | Consumables re-bought at `repurchase_days` ± 20%, probability 60% |
| Weekly pattern | Weekends +20% traffic |
| Daily pattern | Peak 7–11 PM; quiet 1–7 AM |
| Sale window | Aug 10–15 (configurable list of festival/sale windows) |

## 7. Orders & reviews

| Parameter | Value |
|---|---|
| Items per order | 1 (70%) · 2 (20%) · 3+ (10%) |
| Quantity | 1 mostly; consumables 1–3 |
| price_paid | price minus any redeemed offer |
| Review rate | 5% of purchases |
| Review rating | Product quality + noise; text from templates per rating level |

## 8. Offers, consent & responses

| Parameter | Value |
|---|---|
| Starter offers | ~60 across all `offer_type` × `trigger_type` |
| Discount size | 5–40% (capped by `MAX_DISCOUNT_PCT`, never below margin floor) |
| Opted in (consent) | 60% of users |
| Default quiet hours | 22:00 – 08:00 |
| Max offers/week | 3 |
| Simulated open rate | 60% |
| Click rate (of opened) | 25% |
| Redeem probability | `base 10% × (1 + 3 × discount_pct × price_sensitivity)`, × 1.5 if product matches taste |
| "Would buy anyway" | 20% of redeemers (makes uplift analysis meaningful) |

---

**Why these parameters?** They are chosen to look like typical Indian e-commerce behaviour (mobile-heavy, price-sensitive Tier-2/3 users, long-tail catalogue, low conversion) while planting clear patterns that our models should discover and our evaluation can verify against the ground truth.
