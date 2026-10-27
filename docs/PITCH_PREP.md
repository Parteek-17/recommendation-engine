# Pitch prep: Ecommerce Store Personalised Offer Engine

Read this the night before. Practise the pitch out loud with a timer, then have a friend fire the
questions in section 4 at you in random order. Every answer is short on purpose: say the first
sentence, stop, and let them ask for more.

---

## 1. Judge's scorecard (how I'd mark you today)

| Criterion (from the brief) | Your evidence | Risk |
|---|---|---|
| **Core functionality**: offers stay within limits, reach classmates | Guardrails + re-check before send, 7 demo shoppers on @Sutlejbot, 448 tests | Bot must be **up** during judging |
| **Data realism** | 10k users, 1.04M events, planted taste / price comfort / long tail / funnel / repurchase / sale spike / gifting / cold start | They'll ask "how do you know it's realistic?" |
| **Usability** for marketing | Admin dashboard :8502, sliders, live example, `rules.yaml` validated | Strong. Show it live. |
| **Design** of messages + results | Claude writes the wording; codes/prices filled from DB; Results page | Show one real message on your phone |
| **Advanced functionality** | AI chooses + rules overrule, control group, "would buy anyway" → reminder, post-purchase complements, no gender guessing | Strong |
| **Relevance** (business sense) | Cost per extra purchase, profit floor, daily budget, approval above ₹1,000 | Reward points not built (be ready) |

**Fix before you present (30 min total):**
1. `docs/REPORT.md` §8 still says "no Telegram token, Claude in mock mode". That's out of date and a
   judge who opens it will notice. Update or delete those three bullets.
2. Get 5–7 classmates to press Start and pick **different** shoppers before judging, so the Control
   Room's live results are not empty.
3. Laptop: plugged in, sleep **off**, `scripts\run_all.ps1` started from your own terminal, all windows open.

---

## 2. The 3-minute pitch (timed script)

**0:00 – The problem (20 s)**
> "Every discount has a cost. A coupon that saves a customer who's about to leave is money well
> spent. The same coupon to someone who'd buy anyway is pure lost profit. So the real question isn't
> *what* offer, it's *who* should get one at all, and which one."

**0:20 – What we built (30 s)**
> "Our engine watches what shoppers do: a cart left behind, groceries running out, a phone just bought,
> a wishlist item getting cheaper, someone going quiet. For each moment it decides: send nothing,
> send a reminder, or send one specific offer. The offer arrives on Telegram."

**0:50 – The key idea: AI proposes, rules decide (40 s)**
> "Claude is allowed to *choose*, but only from a shortlist our rules already approved, and it can
> also choose 'no discount'. If it picks anything outside that list, the system rejects it, logs it,
> and uses the rec engine's pick instead. Claude writes the words; the code, price and expiry date
> always come from our database, never from the AI. Every step writes an audit row, so any offer can
> be explained."

**1:30 – Live demo (60 s)**: see section 3.

**2:30 – Proof it works (20 s)**
> "In simulation on our data, the engine gets 2.6 times more *extra* purchases than 'give everyone
> the biggest discount', at half the cost per extra purchase: ₹110 against ₹222. Bigger discounts
> were actually *worse*, because they weren't on the thing the customer wanted. And 10% of customers
> get nothing on purpose, a control group, so with real users we can measure true uplift."

**2:50 – Close (10 s)**
> "So: a marketing team sets the limits without code, the AI personalises inside them, and nothing
> leaves the building without passing the rules. Try it: @Sutlejbot."

---

## 3. Demo run sheet

Open **before** you start: Telegram on your phone (screen mirrored if possible), Admin dashboard
(`localhost:8502`) in a browser tab.

1. **Phone:** `/start` on @Sutlejbot → pick **🎓 Budget student**. Read out the offer
   ("8% off the dal you left in your cart") and the *Behind the scenes* note: what Claude chose and
   which rules it passed.
2. **Phone:** `/switch` → **💎 Premium regular**. Point out: *no discount, just a reminder.*
   "This person buys every month anyway, so a coupon would only cost us money."
3. **Admin dashboard → 📏 Offer rules:** drag *max discount* from 40 to 15 and show the live example
   change **before** saving. "A marketing person can do this; a typo can't switch a limit off."
4. **Admin dashboard → 📈 Results:** the strategy chart and "Claude accepted vs overruled".
5. **Admin dashboard → 🤖 Assistant:** click *"Make a 15% festival offer on Footwear…"*. Claude drafts it,
   the rules check it, and you press **Create offer**. Then ask for 70% off: the rules refuse it, not Claude.
   "Even our own admin chatbot can only propose. The rules decide."

**If the bot is slow or down:** say "it's calling Claude live, this takes a few seconds", and if
it really fails, switch to the Admin dashboard's 🧪 Demo testers page, which shows what each tester
received. Never debug live.

---

## 4. Questions you'll get, with answers

### Core functionality and guardrails

**Q: How do you guarantee an offer never breaks your rules?**
Two checks. Offers are filtered by the guardrails before Claude sees them (max discount, profit
floor, no stacking, age limits, max uses, already sent, approval above ₹1,000, daily budget). Then,
right before sending, the chosen offer is checked **again** (`revalidate`), because things can
change in between: stock, budget, another offer sent. Fail either one and it's not sent; the reason
goes to `audit_log`.

**Q: What if Claude hallucinates an offer or a price?**
It can't put one into the message. Claude returns an offer ID from the shortlist or `no_discount`.
Anything else is rejected and logged, and the rec engine's top pick is used. When writing, Claude
must use placeholders like `{CODE}`, `{DISCOUNT}` and `{EXPIRY}`, and its draft is **rejected if it contains any
number, ₹ or %**, a link or fake urgency. We fill the placeholders from the database afterwards, so
a wrong price simply cannot get into the message.

**Q: What happens if Claude is down or slow?**
10-second timeout. Then the ranker's pick and a plain template message are used. The customer still
gets a valid offer, just less nicely worded. There's a test for this.

**Q: Why let the AI choose at all, if rules decide?**
The rules decide what's *allowed*; the AI decides what *fits this person* among allowed options. It
can read the whole situation, like "premium shopper, item already in cart, choose no discount", which is
awkward to write as rules. And the Results page shows how often it was overruled, so you can judge
whether it's earning its place.

**Q: How do you stop spamming people?**
Max 1 offer per day, 3 per week, quiet hours 22:00–08:00, and they must have pressed Start (opt-in).
`/stop` opts out. All of these are adjustable in the Admin dashboard.

**Q: What's "stacking"?**
Two coupons that could be used on the same purchase. We block a new offer if the customer already
holds a live one that overlaps it. Honest weakness: one long-lived store-wide coupon can block new
offers for a long time; the fix is letting unused offers expire from the check after ~14 days.

### Data realism

**Q: How is your synthetic data realistic?**
We planted behaviour real stores see, then checked our models had to *find* it:
each shopper has 2–3 favourite categories and a price comfort zone; popularity has a long tail (few
hits, many rarely bought items); a funnel of view → cart → buy with drop-off; groceries rebought on cycles;
after buying a phone, interest in other phones drops and in accessories rises; evenings and
weekends are busier; an August sale spike; gift sessions; brand-new users and products. The hidden
"truth" is stored separately and **never** given to the models.

**Q: Numbers?**
10,000 users (519 minors), 3,000 products from ₹39 to ₹79,809, 1.04 million events, 29,678
orders, April–September 2026. About 60% views, 25% clicks, 9% add-to-cart, 4% purchases.

**Q: Couldn't you just be fitting the patterns you planted yourself?**
Yes, and we say so. The simulation shows the engine *uses* the planted signals (taste, price
sensitivity, matching the item). It doesn't prove real-world uplift. That's why we built the
control group: run it live and compare who got offers with who didn't. The system also loads real
datasets (RetailRocket, Amazon) through one standard format, if anyone wants to test on real data.

### Usability

**Q: How does a marketing person make an offer?**
Two ways. The 🎁 Create offer page has three steps (what kind, on what, for whom). Products are found by
name, and a live coupon preview and rule check update as they type. Or they ask the 🤖 Assistant in plain
English. Both go through the same rule check (`offers/manual.py`), an offer that breaks a rule can't
be saved, and big ones wait for a manager's OK on the 📋 Offers page.

**Q: What can the admin assistant do? Isn't it risky?**
It answers daily questions (sales, approvals, blocked offers, best sellers, rules) with read-only
database tools, and can *draft* offers. It can't save, approve, change rules, message customers or see
keys. Every number comes from a tool. Without Claude, a simple keyword version answers from the same tools.

**Q: Could a marketing person really use this without help?**
Show it. Every rule is a slider with a plain-English label, and a live example shows the effect before
saving. It writes to one commented file, `offers/rules.yaml`. A bad value is refused with a message,
and the system keeps the last good rules, so a mistake can never switch a limit off.

**Q: Do changes need a restart?**
No. The pipeline re-reads the file on every run if it changed.

### Design

**Q: Who writes the messages?**
Claude (the fast model) writes the wording, short and in a friendly Indian-marketplace tone, with
inline buttons. The facts in it come from the DB. If Claude fails, a clean template is used.

**Q: Why does the reason say "the shopper" and never "he/she"?**
We told the AI never to guess gender from a name. It's both respectful and safer.

### Advanced functionality

**Q: What's the hardest case you handle?**
Pick one and tell it well:
- **Would buy anyway:** premium regulars with frequent orders get a reminder, not a discount. That's
  the exact problem in the brief.
- **Post-purchase:** after a phone, never offer another phone; offer a case or charger bundle. We
  found and fixed a bug where accessories were hidden too; recommendation quality went from 0.253
  to 0.269 NDCG.
- **Minors** never see age-restricted categories.

**Q: What's the control group for?**
10% of customers (chosen by a stable hash of their ID) get nothing. Comparing their purchases with
everyone else's tells you what offers *add*, not just how many were redeemed. Classmate testers are never held
out, so the demo always works.

**Q: How does the recommendation engine work?**
Four ways to find candidates (popularity, "people who bought this also bought", ALS matrix
factorisation, text similarity). A LightGBM model ranks them, then business rules re-rank: in stock,
not already owned, budget, variety. Every recommendation carries a reason.

**Q: How good is it?**
The ranker gets 2.6× the accuracy of plain popularity (NDCG@10, on a test window the models never
saw). The full pipeline is lower, 1.7×, **on purpose**: it hides things you already own, which looks
"accurate" offline but is useless to show.

### Relevance and business sense

**Q: Is this worth the money for a real company?**
The metric we optimise is cost per *extra* purchase, not redemptions. ₹110 for the engine, ₹222 for
"biggest discount". Plus hard brakes: daily discount budget ₹50,000, profit floor, human approval for
any offer over ₹1,000.

**Q: But your engine spends more in total than the generic offer.**
True: about ₹1,240 vs ₹565 per 1,000 triggers, because more of its offers actually get used. Per
extra sale it's the same as generic (₹110 vs ₹111) but it produces more than twice as many extra
sales (11.3 vs 5.1). If budget is the limit, marketing turns the daily budget down.

**Q: The brief mentions reward points. Do you use them?**
Not yet. The data contract already has a `points` offer type, so it's a new offer kind plus a points
balance column, not a redesign. It was the next item on our list.

**Q: Win-back looked expensive.**
Yes, ₹777 per extra purchase, because lapsed customers need bigger nudges. That's a business choice:
marketing can lower the win-back starting discount in the Admin dashboard in five seconds.

### Engineering

**Q: How did you use Claude Code?**
Planning first (CLAUDE.md is our single source of truth: data contract, module contracts,
decisions log), then built in phases: data, reco, offers, LLM, bot, API/UI, pipeline. Claude Code
wrote, tested and debugged; we reviewed and steered.

**Q: How are your keys kept safe?**
Only in `.env`, which is git-ignored. `.env.example` has blanks. Keys are never logged, never in a
prompt, never in the frontend. The Offers API has its own internal key.

**Q: How is it tested?**
448 automated tests, including end-to-end: trigger → offer → Telegram (fake bot) → button click →
event logged. Plus tests for AI-rejected choices, the control group and the rules file.

**Q: Would it scale?**
Not as-is: SQLite on one laptop, long polling. Production would mean Postgres, a Telegram webhook
and a job queue. The design (stateless pipeline steps with an audit log) carries over.

---

## 5. Numbers cheat sheet

| What | Number |
|---|---|
| Users / products / events | 10,000 / 3,000 / 1,038,441 |
| Data window | 1 Apr – 30 Sep 2026 |
| Extra purchases vs "biggest discount" | **2.6×** (11.3 vs 4.3 per 1,000 triggers) |
| ₹ per extra purchase | **₹110** engine · ₹111 generic · ₹124 random · ₹222 max discount |
| Cart abandon redemptions | 17.0 vs 1.3 (max discount) per 1,000 |
| Reco accuracy vs popularity | ranker 2.6× · full pipeline 1.7× (NDCG 0.269) |
| Complements found | 46% of true pairs, 0% substitutes mixed in |
| Already-owned lookalikes in recs | 22% → 2.9% after rules |
| Guardrails kept | 25% of ranked offers (mostly stacking removed) |
| Limits | max 40% off · profit floor 2% · 1/day, 3/week · 22:00–08:00 · ₹50k/day · approval > ₹1,000 |
| Control group | 10% |
| Tests | 448 passing |

---

## 6. Don't say

- **"Our engine is proven to work."** Say "in simulation on our data". Claim real uplift only from the control group.
- **"The AI decides."** Say "the AI chooses, the rules decide".
- **"Half the cost"** without "versus biggest discount". Against the generic offer it's the same cost with twice the extra sales.
- Don't read code on screen. Show the bot, the Admin dashboard and one audit row at most.
