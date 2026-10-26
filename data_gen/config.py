"""Every knob of the synthetic data generator, in one place.

Values come from docs/DATA_PARAMETERS.md. Change them HERE, never inside the other files.
Everything random is seeded with SEED, so the same seed always produces identical data.

The catalog itself (category names, brands, product types, complement rules) lives in
data_gen/templates.py because it is a long list of words, not a "knob".
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. Global
# ---------------------------------------------------------------------------
SEED = 42
WINDOW_START = "2026-04-01"   # first day of simulated behaviour
WINDOW_END = "2026-09-30"     # last day (inclusive)

# `activity_scale` multiplies every user's session rate. The activity ranges in section 4
# (e.g. heavy = 15-30 sessions/month) are relative levels; this factor tunes the TOTAL
# number of events to the targets below (~80k small, ~1M full) without changing the shape.
SIZES: dict[str, dict] = {
    "tiny": {"users": 200, "products": 150, "activity_scale": 0.30},      # unit tests (a few seconds)
    "small": {"users": 1_000, "products": 500, "activity_scale": 0.30},   # ~80k events, < 1 minute
    "full": {"users": 10_000, "products": 3_000, "activity_scale": 0.30},  # ~1M events
}

# ---------------------------------------------------------------------------
# 2. Products (catalog)
# ---------------------------------------------------------------------------
# Parent category -> (min, max) price in ₹. Product prices are log-uniform inside the
# product type's own range, which is always kept inside these parent limits.
PARENT_PRICE_RANGES: dict[str, tuple[int, int]] = {
    "Electronics": (300, 80_000), "Fashion": (200, 5_000), "Home & Kitchen": (150, 15_000),
    "Beauty": (100, 2_500), "Grocery": (30, 1_500), "Sports": (200, 10_000),
    "Books": (99, 1_200), "Toys": (150, 4_000),
}
# Parent category -> (min, max) margin %.
PARENT_MARGINS: dict[str, tuple[float, float]] = {
    "Electronics": (5, 15), "Grocery": (5, 12), "Books": (10, 25), "Home & Kitchen": (20, 35),
    "Sports": (20, 35), "Beauty": (25, 40), "Toys": (25, 40), "Fashion": (30, 50),
}
MRP_MARKUP_MAX = 0.40               # mrp = price × (1 + uniform(0, 0.40))
RATING_MEAN, RATING_SD = 4.0, 0.4   # hidden "quality" ~ Normal(4.0, 0.4), clipped 1-5
RATING_COUNT_TOP = 3_000            # bestseller gets ~3,000 ratings, the long tail only a few
ZIPF_EXPONENT = 1.1                 # long-tail popularity
OUT_OF_STOCK_RATE = 0.05
LATE_LAUNCH_RATE = 0.10             # cold-start items ...
LATE_LAUNCH_WEEKS = 6               # ... launched in the last 6 weeks of the window
OLDEST_LAUNCH = "2024-01-01"        # all other products launched between this date and WINDOW_START
STOCK_RANGE = (5, 500)

# ---------------------------------------------------------------------------
# 3. Product relations (ground truth)
# ---------------------------------------------------------------------------
COMPLEMENTS_PER_PRODUCT = (1, 5)    # where a complement rule exists
COMPLEMENTS_PER_KIND = (1, 2)       # how many products to take from each complement product type
SUBSTITUTE_PRICE_BAND = 0.30        # same sub-category + same product kind + price within ±30%

# ---------------------------------------------------------------------------
# 4. Users
# ---------------------------------------------------------------------------
AGE_GROUPS = {"13-17": 0.05, "18-24": 0.30, "25-34": 0.35, "35-44": 0.18, "45+": 0.12}
GENDERS = {"F": 0.45, "M": 0.53, "Other": 0.02}     # descriptive only — never used for offers
CITY_TIERS = {1: 0.40, 2: 0.35, 3: 0.25}
DEVICES = {"mobile": 0.75, "desktop": 0.25}
OLDEST_SIGNUP = "2025-01-01"        # users who joined before the window
COLD_START_USER_RATE = 0.10         # share of users who sign up in the last month
COLD_START_USER_DAYS = 30

PREF_CATEGORIES = (2, 3)            # preferred sub-categories per user
PREF_DIRICHLET_ALPHA = 1.0          # how unevenly a user splits interest across them
PREF_SAME_PARENT_PROB = 0.5         # e.g. Mobiles + Mobile Accessories go together
PRICE_SENS_BETA = (2.0, 2.0)        # 0 = premium buyer, 1 = bargain hunter
TIER3_SENS_SHIFT = 0.10
ACTIVITY_LEVELS: dict[str, tuple[float, tuple[int, int]]] = {
    # level: (share of users, (min, max) sessions per month before activity_scale)
    "light": (0.50, (1, 2)),
    "regular": (0.35, (4, 8)),
    "heavy": (0.15, (15, 30)),
}
GIFTER_RATE = 0.15

# ---------------------------------------------------------------------------
# 5. Behaviour simulation
# ---------------------------------------------------------------------------
EVENTS_PER_SESSION = (3, 15)
TASTE_PROB = 0.80                   # 80% of browsing in preferred categories, 20% exploring
SEARCH_SESSION_RATE = 0.10
GIFT_SESSION_RATE = 0.05            # share of a gifter's sessions that are gift sessions
BROWSE_ONLY_SESSION_RATE = 0.30     # "abandoned" sessions: just looking, nothing goes in the cart
NOISE_CLICK_RATE = 0.05             # random product, any category
VIRAL_PICK_RATE = 0.03              # chance a step shows a currently-viral product (home page)
SECOND_DEVICE_RATE = 0.10           # sessions on the user's other device

# Funnel base rates (per viewed product / per cart item)
CLICK_RATE = 0.30                   # not in DATA_PARAMETERS: our choice for view -> click
WISHLIST_RATE = 0.05
ADD_TO_CART_RATE = 0.10
REMOVE_FROM_CART_RATE = 0.15
PURCHASE_RATE = 0.30                # of cart items
# Multipliers on those rates
TASTE_MULT = 2.0                    # product in user's preferred categories (or gift intent)
COMFORT_MULT, OUTSIDE_COMFORT_MULT = 1.5, 0.4
COMFORT_WIDTH = 0.30                # comfort = price percentile within ±0.30 of the user's target
HIGH_RATING, HIGH_RATING_MULT = 4.3, 1.2
MAX_PROB = 0.90                     # no funnel step is ever (almost) certain
# Clicks, wishlists and purchases use a SOFTENED multiplier sqrt(m / TYPICAL_MULT), so the
# realised rates stay near the base rates above (view->click ~30%, cart->purchase ~30%) while
# taste and price comfort still clearly matter. add_to_cart uses the full multiplier.
TYPICAL_MULT = 2.0

# Calendar effects
SALE_WINDOWS: list[tuple[str, str, str]] = [
    ("2026-08-10", "2026-08-15", "Independence Day Sale"),
]
SALE_TRAFFIC_MULT = 2.0
SALE_CONVERSION_MULT = 1.8
WEEKEND_TRAFFIC_MULT = 1.2
# Relative traffic per hour of day (0-23): quiet 1-7 AM, peak 7-11 PM.
HOUR_WEIGHTS = [3, 1, 1, 1, 1, 1, 1, 2, 4, 5, 5, 5, 6, 6, 5, 5, 5, 6, 7, 10, 11, 11, 10, 6]
VIRAL_PRODUCTS = 10
VIRAL_DAYS = (14, 21)
VIRAL_VIEW_MULT = 5.0

# Planted patterns
SUBSTITUTE_AFTER_PURCHASE_MULT = 0.1   # interest in substitutes after buying a non-consumable ...
SUBSTITUTE_AFTER_PURCHASE_DAYS = 60    # ... for 60 days
COMPLEMENT_AFTER_PURCHASE_MULT = 3.0   # interest in complements ...
COMPLEMENT_AFTER_PURCHASE_DAYS = 14    # ... for 14 days
COMPLEMENT_BUY_PROB = 0.25             # chance to come back and buy a complement
COMPLEMENT_NUDGE_PROB = 0.30           # chance a normal session shows a boosted complement
REPURCHASE_PROB = 0.60                 # consumables re-bought ...
REPURCHASE_JITTER = 0.20               # ... at repurchase_days ± 20%

# ---------------------------------------------------------------------------
# 7. Orders & reviews
# ---------------------------------------------------------------------------
ITEMS_PER_ORDER = {1: 0.70, 2: 0.20, 3: 0.07, 4: 0.03}   # extra items: complements first ("bought together")
CONSUMABLE_QTY = (1, 3)
REVIEW_RATE = 0.05
REVIEW_DELAY_DAYS = (3, 14)
REVIEW_NOISE_SD = 0.8

# ---------------------------------------------------------------------------
# 8. Offers, consent & simulated responses
# ---------------------------------------------------------------------------
N_OFFERS_TARGET = 60                # ~60 starter offers across every offer_type × trigger_type
MAX_DISCOUNT_PCT = 40               # same as the .env default (kept here so the data never depends on .env)
APPROVAL_THRESHOLD_INR = 1000       # flat offers this big are created with requires_approval = 1
OFFERS_START = "2026-07-01"         # starter offers are valid from here (sends simulated from here)
OFFERS_END = "2027-03-31T23:59:59"
CONSENT_RATE = 0.60                 # minors are never opted in
DEFAULT_QUIET = ("22:00", "08:00")
ALT_QUIET = ("23:00", "07:00")      # some users chose different quiet hours
ALT_QUIET_RATE = 0.15
MAX_OFFERS_PER_WEEK = 3
LOWER_WEEKLY_CAP_RATE = 0.20        # users who asked for 1-2 offers/week instead of 3
STATED_PREFS_RATE = 0.40            # opted-in users who told us 1-2 favourite categories
SEND_HOURS = (10, 21)               # sends happen outside quiet hours
HOLDOUT_RATE = 0.20                 # eligible trigger, deliberately NOT sent (control group for uplift)
FAILED_RATE = 0.02                  # Telegram delivery failures
OPEN_RATE = 0.60
CLICK_OF_OPENED = 0.25
REDEEM_BASE = 0.10                  # of clicked: base × (1 + 3 × discount × price_sensitivity)
REDEEM_DISCOUNT_FACTOR = 3.0
REDEEM_TASTE_MULT = 1.5
WOULD_BUY_ANYWAY = 0.20             # control users buy anyway at 20% of the treated redemption rate
WIN_BACK_DAYS = 45                  # no session for 45 days -> win_back trigger (checked Mondays)
PRICE_DROP_PROB = 0.30              # wishlisted yesterday -> price_drop trigger
FESTIVAL_REACH = 0.25               # share of opted-in users targeted on a sale's first day
