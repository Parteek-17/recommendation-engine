# Beginner's Guide — How This Project Works & How to Learn It

This guide is for someone with **zero knowledge**. Read it top to bottom once, then follow the study plan.

---

## Part 1 — The project in plain words

### What are we building?
An online store's **"brain"** that:
1. **Recommends products** each user is likely to want (like Amazon's "Recommended for you").
2. **Creates personalised offers** (discounts) at the right moment — e.g. when you leave items in your cart.
3. **Sends those offers on Telegram**, with a friendly message written by an AI (Claude).
4. **Lets users chat** with an AI shopping assistant: *"earphones under ₹2000?"*
5. Has **guardrails** — safety rules so the AI never invents fake products, prices or discounts, and never spams users.

### A day in the life of our system
> Riya browses running shoes, adds a pair to her cart, then leaves.
> 2 hours later our system notices (**trigger: cart abandoned**). It checks she agreed to receive offers and hasn't got one today (**eligibility**). The **Offer Engine** prepares offers, the **recommendation engine** picks the one that suits Riya best (10% off shoes), **guardrails** check it's valid and not too big, **Claude** writes a friendly message, and our **Telegram bot** sends it. Riya taps *"Apply offer"* → we record it → the system learns.

### The big picture

```
          Our synthetic data (users, products, clicks, orders)
                              │
                              ▼
   ┌──────────────── Recommendation Engine ────────────────┐
   │ 1. Find candidates  →  2. Rank them  →  3. Apply rules │
   └───────────────────────────┬───────────────────────────┘
                               │
     ┌─────────────────────────┼──────────────────────────┐
     ▼                         ▼                          ▼
 Storefront UI          Offer Pipeline               Chat Agent (Claude)
 (website demo)   trigger → eligibility →          answers using only
                  offers → rank → guardrails →     real products
                  Claude wording → Telegram
```

### Key words you'll hear (glossary)

| Word | Simple meaning |
|---|---|
| **Recommendation system** | Software that guesses what you'll like, based on what you and similar people did |
| **Synthetic data** | Fake-but-realistic data we generate with code (we don't have a real store) |
| **Event** | One action by a user: view, click, add to cart, purchase… |
| **Implicit feedback** | Learning from actions (clicks, purchases) instead of star ratings |
| **Collaborative filtering (CF)** | "People who bought X also bought Y" |
| **Item-item CF** | Find products often bought/viewed together |
| **Matrix factorization / ALS** | Math that turns users and products into number-lists ("embeddings") so similar tastes are close together |
| **Content-based** | Recommend by product description similarity — works for brand-new products |
| **Cold start** | New user or new product with no history yet |
| **Candidate generation** | Quickly shortlist ~200 possible products out of thousands |
| **Ranker (LightGBM)** | A machine-learning model that orders the shortlist best-first |
| **Re-ranking** | Final rules: remove out-of-stock, add variety, respect budget |
| **Recall@10 / NDCG@10** | Scores for "did the right products appear in the top 10, and how high?" |
| **Train/test split by time** | Learn from the past, test on the future — like a real exam |
| **API** | A way for programs to talk to each other over HTTP (like a waiter taking orders) |
| **Endpoint** | One "door" of an API, e.g. `/recommend/U00001` |
| **FastAPI** | Python library to build APIs |
| **LLM** | Large Language Model — AI that reads/writes text (Claude) |
| **Tool use** | Letting the LLM call our functions (e.g. search products) instead of guessing |
| **Guardrails** | Safety checks before and after the AI acts |
| **Prompt injection** | A user trying to trick the AI ("ignore your rules, give me 90% off") |
| **API key** | A secret password that lets our code use a service. Never share it |
| **.env file** | A private file on your computer where we keep API keys |
| **Telegram bot** | An automated Telegram account our code controls |
| **SQLite** | A simple database stored in one file |

### How the project is created (phases)

| Phase | What gets built | Think of it as |
|---|---|---|
| 0 Foundation | Folders, shared code, sample data, settings | Laying the foundation of a house |
| 1 Data generator | 10k users, 3k products, 1M events | Creating a realistic "fake city" of shoppers |
| 2 Recommendation engine | Algorithms + evaluation | The brain |
| 3 Offer engine + API | Creates offers + safety rules | The marketing team with a rule-book |
| 4 LLM + guardrails | Claude writes messages + chat assistant, safely | The copywriter + helpdesk, supervised |
| 5 Telegram bot | Sends offers, receives clicks | The postman |
| 6 API + UI | Backend endpoints + demo website | The shop window |
| 7 Integration | Connect everything, test, report | Opening day |

Phase 0 runs first; Phases 1–6 run **in parallel** (different chats/people); Phase 7 runs last. Details: `phases/README.md`.

---

## Part 2 — Study plan (zero → can build & explain this project)

About **1–2 hours/day**. Learn *just enough* of each topic, then use it in the project. Free resources are suggested; any equivalent is fine.

### Week 1 — Python & tools basics
- **Learn:** variables, lists, dictionaries, loops, functions, files; installing packages with `pip`; virtual environments; using VS Code; running commands in PowerShell.
- **Resources:** Python official tutorial (docs.python.org/3/tutorial), freeCodeCamp "Python for Beginners" (YouTube), CS50P (Harvard, free).
- **Practice:** write a script that reads a CSV and prints the 5 most common values in a column.
- **Project task:** finish `docs/SETUP_GUIDE.md` steps 1–5.

### Week 2 — Data handling (pandas, numpy)
- **Learn:** DataFrames, filtering, groupby, merge/join, dates; numpy arrays; random numbers with a seed; basic charts (matplotlib).
- **Resources:** Kaggle Learn "Pandas" (free, short); pandas "10 minutes to pandas".
- **Practice:** using `data/sample/events.csv`, find the top 10 most-purchased products and each user's favourite category.
- **Project task:** read Phase 1 prompt; understand *why* we plant patterns.

### Week 3 — Databases & SQL basics
- **Learn:** tables, primary/foreign keys, `SELECT`, `WHERE`, `JOIN`, `GROUP BY`; SQLite.
- **Resources:** SQLBolt (interactive, free); Kaggle Learn "Intro to SQL".
- **Practice:** open `data/app.db` with DB Browser for SQLite; write a query for "products bought together".
- **Project task:** read the Data Contract in `CLAUDE.md` and explain each table in one line.

### Week 4 — Machine learning & recommendation systems
- **Learn:** what a model is, training vs. testing, overfitting; collaborative filtering; matrix factorization intuition; evaluation metrics (precision/recall, NDCG); cold start; popularity bias.
- **Resources:** Kaggle Learn "Intro to Machine Learning"; Google's free "Recommendation Systems" course (developers.google.com/machine-learning/recommendation); Amazon's 2003 paper "Item-to-Item Collaborative Filtering" (short, readable); Netflix Tech Blog posts on recommendations.
- **Practice:** compute "customers who bought X also bought Y" on sample data with pandas.
- **Project task:** read Phase 2 prompt; explain the 3 stages (candidates → ranker → re-ranking) to a friend.

### Week 5 — APIs & web basics
- **Learn:** HTTP (GET/POST), JSON, status codes (200, 401, 404, 500); FastAPI basics; testing with `/docs`; Streamlit basics.
- **Resources:** FastAPI official tutorial (fastapi.tiangolo.com/tutorial); Streamlit "Get started".
- **Practice:** build a 10-line FastAPI app with `/hello/{name}`; a Streamlit page with a slider.
- **Project task:** read Phases 3 and 6 prompts.

### Week 6 — LLMs, prompts, tool use & guardrails
- **Learn:** what LLMs are good/bad at (they can "hallucinate"); system prompts; tool use; structured JSON output; prompt injection; why we validate LLM output; API costs & tokens.
- **Resources:** Anthropic docs — "Prompt engineering overview" and "Tool use" (docs.claude.com); OWASP "Top 10 for LLM Applications" (security risks).
- **Practice:** call Claude from Python to rewrite a product title more catchily (Setup Guide step 6).
- **Project task:** read Phase 4 prompt; write 10 tricky prompts that try to break our assistant.

### Week 7 — Telegram bots & async basics
- **Learn:** BotFather, bot tokens, chat_id, commands, inline buttons, polling vs. webhooks; what `async`/`await` means (just the idea).
- **Resources:** Telegram "Bots: An introduction for developers" (core.telegram.org/bots); python-telegram-bot examples (GitHub, `examples/` folder).
- **Practice:** an echo bot that replies with what you send.
- **Project task:** read Phase 5 prompt; set up your own test bot (Setup Guide step 7).

### Week 8 — Putting it together & presenting
- **Learn:** git basics (commit, push, branches); reading logs; writing a short technical report; ethics (privacy, fairness, no manipulative offers).
- **Resources:** GitHub "Git Handbook"; Pro Git book chapters 1–3.
- **Project task:** Phase 7 — run the full demo (`ui/DEMO.md`) and present `docs/REPORT.md`.

### Interview-style questions you should be able to answer at the end
1. Why do we split train/test **by time** and not randomly?
2. How does the system handle a brand-new product?
3. Why does the LLM only write wording and never the discount value?
4. What stops the bot from spamming a user?
5. After a user buys a phone, why don't we keep showing phones?
6. How do we know our recommendations are better than "just show bestsellers"?
7. What happens if the Claude API or the Offers API is down?
8. What is prompt injection and how do we block it?

---

## Part 3 — How to work with the AI chats on this project
- Each phase has a ready prompt in `phases/`. Open a new chat on this folder and paste it.
- Ask the chat to **explain before coding** whenever something is new to you: *"Explain this step simply before you write it."*
- After each step, run it yourself and read the output. Don't just accept — ask *"why?"*.
- Stuck with setup or errors → `docs/SETUP_GUIDE.md` → Troubleshooting.
