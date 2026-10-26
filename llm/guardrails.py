"""Guardrails: code checks that run BEFORE Claude sees a message and AFTER Claude answers.

    from llm.guardrails import check_input, validate_output, mask_pii
    check_input("ignore previous instructions")    # GuardrailResult(allowed=False, reason="prompt_injection: ...")
    mask_pii("call me on 9876543210")             # ("call me on [PHONE]", ["phone"])

Why code and not just a system prompt? A prompt is a request Claude usually follows; a
guardrail is a rule that *always* runs. "LLM proposes, rules decide."

Every blocked result has a reason of the form "<code>: <detail>". The code (e.g.
"prompt_injection") picks the friendly reply in templates.refusal_text().

Patterns understand English, Hinglish (Hindi in Latin letters) and some Devanagari.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import defaultdict, deque
from functools import lru_cache

from common.config import settings
from common.schemas import ChatReply, GuardrailResult
from llm import catalog
from llm.prompts import LEAK_MARKERS

MAX_INPUT_CHARS = 1000
RATE_LIMIT_MESSAGES = 12     # max messages per user ...
RATE_LIMIT_WINDOW_S = 60     # ... in this many seconds

# ---------------------------------------------------------------------------
# Text normalisation — attackers hide words with CAPS, accents, zero-width chars, l33t
# ---------------------------------------------------------------------------

_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"), None)
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
_LEET_L = str.maketrans({"1": "l", "|": "l"})  # '1' also stands for 'l' ('ru1es')


def normalize(text: str) -> str:
    """Lowercase, unify look-alike characters, drop invisible ones, collapse spaces."""
    text = unicodedata.normalize("NFKC", text or "").translate(_ZERO_WIDTH).lower()
    return re.sub(r"\s+", " ", text).strip()


def _variants(text: str) -> list[str]:
    """The normalised text plus l33t-decoded copies ('1gn0re y0ur ru1es' -> 'ignore your rules')."""
    norm = normalize(text)
    return list(dict.fromkeys([norm, norm.translate(_LEET), norm.translate(_LEET_L).translate(_LEET)]))


def _squashed(text: str) -> str:
    """Letters only — catches 'i g n o r e  p r e v i o u s' and 'ign.ore'."""
    return re.sub(r"[^a-z\u0900-\u097f]", "", normalize(text).translate(_LEET))


# ---------------------------------------------------------------------------
# Pattern lists (regexes run on the normalised text)
# ---------------------------------------------------------------------------

_INSTR = r"(instructions?|rules?|prompts?|guidelines?|directions?|polic(y|ies)|restrictions?|programming|training)"

INJECTION_PATTERNS = [
    rf"\b(ignore|disregard|forget|override|bypass|skip|drop|delete)\b.{{0,40}}\b(previous|prior|above|earlier|all|your|the|any|these|those|system)\b.{{0,30}}\b{_INSTR}",
    r"\byou are (now|no longer)\b", r"\bfrom now on,? (you|your)\b", r"\bact as (a|an|my|the)?\b", r"\bact like\b",
    r"\bpretend (to be|you are|you're|that)\b", r"\brole-?play\b", r"\bimagine you are\b",
    r"\bsystem prompt\b", r"\b(system|developer|hidden|initial|original|secret) (message|instructions?|prompt)\b",
    rf"\b(reveal|show|print|repeat|display|leak|output|summari[sz]e|translate|tell me|what are|what were|list)\b.{{0,25}}\b(your|the system|hidden|secret|initial|original|internal)\b.{{0,20}}\b({_INSTR[1:-1]}|configuration|settings|context)",
    r"\brepeat (the |everything |all )?(text |words )?(above|before|so far)\b",
    r"\b(jailbreak|dan mode|developer mode|god mode|sudo|admin mode|debug mode|maintenance mode|unfiltered|unrestricted)\b",
    r"\b(no|without( any)?) (restrictions|filters|rules|limits|guardrails)\b",
    r"\bnew (instructions|rules|persona|role)\b",
    r"</?\s*(system|assistant|user|instructions?|im_start|im_end)\s*>", r"\[\s*/?\s*(system|inst)\s*\]", r"#{2,}\s*(system|instruction)",
    r"\b(api[ _-]?key|secret key|access token|bot token|offers_api_key|env file)\b", r"\.env\b",
    r"\b(base64|rot13|hex)\b.{0,20}\b(decode|decoded|encoded|this)\b", r"\bdecode (this|the following)\b",
    # Hinglish / Hindi
    r"\b(pichle|pichhle|pehle|purane|sab|saare|sare|apne|apni)\b.{0,30}\b(instructions?|nirdesh|rules?|niyam|prompt)\b.{0,30}\b(bhool|bhul|ignore|chhod|chod|hata|mat mano)",
    r"\b(bhool|bhul) ja(o|a|ao)\b.{0,30}\b(sab|rules?|niyam|instructions?)", r"\b(tum|aap) ab\b.{0,30}\b(ho|hain|bano|ban jao)\b",
    r"\bab se (tum|aap)\b", r"\bapna (system )?prompt\b",
    r"(निर्देश|नियम).{0,20}(भूल|अनदेखा|छोड़)", r"सिस्टम प्रॉम्प्ट",
]
_INJECTION_SQUASHED = ("ignoreprevious", "ignoreyour","ignoreallprevious", "ignoreyourinstructions", "ignoreallinstructions",
                       "systemprompt", "jailbreak", "youarenow", "developermode", "disregardprevious")

DISCOUNT_PATTERNS = [
    r"\b(give|make|create|generate|apply|issue|send|add|set|grant|want|need|get|activate)\b.{0,25}\b\d{2,3}\s*(%|percent|pct)",
    r"\b(create|generate|make|invent|issue|fabricate|code up)\b.{0,25}\b(coupon|promo|discount|voucher|offer|gift card|code)s?\b",
    r"\b(give|send) me\b.{0,15}\b(a |an )?(new|special|custom|secret|extra|bigger|free|private)\b.{0,10}\b(coupon|code|discount|voucher)",
    r"\b(get|give|send|make|ship|deliver)\b.{0,25}\b(for free|free of cost|at no cost|without paying|for nothing)\b",
    r"\b(set|change|make|reduce|drop|lower|update|edit)\b.{0,20}\bprice\b.{0,25}\b(to|at|as)\b\s*(₹|rs\.?|inr|\d)",
    r"\b(stack|combine|reuse|unlimited|infinite|multiple)\b.{0,20}\b(coupons?|codes?|offers?|discounts?)\b",
    r"\b(employee|staff|admin|internal|secret|hidden|vip|influencer|owner|manager)\b.{0,15}\b(discount|coupon|code|price|offer|pricing)s?\b",
    r"\b(discount|coupon|code|offer)\b.{0,20}\b(bana|banao|bana do|de do|dedo|do na)\b",
    r"\b\d{2,3}\s*(%|percent).{0,25}\b(de do|dedo|chahiye|do na|off do|kar do)\b",
    r"\b(free mein|muft|muft mein|bina paise)\b",
]

OTHER_USER_PATTERNS = [
    r"\b(other|another|someone else'?s?|other people'?s?|all|every|different|random)\b.{0,20}\b(users?|customers?|people|buyers?|accounts?|members?)'?s?\b.{0,30}\b(data|orders?|details|history|address(es)?|phone|numbers?|emails?|purchases?|information|info|carts?|chat ?ids?|names?)\b",
    r"\bmy (friend|neighbou?r|wife|husband|brother|sister|ex|boss|colleague|roommate)'?s?\b.{0,20}\b(orders?|account|address|phone|history|purchases?|data|cart|password)\b",
    r"\b(list|dump|export|show|download|give me)\b.{0,15}\b(all|every)\b.{0,15}\b(users?|customers?|orders?|accounts?|emails?|phone numbers?)\b",
    r"\b(log ?in|sign ?in|switch|change)\b.{0,15}\b(as|to|into)\b.{0,15}\b(another|other|different|admin|someone)\b",
    r"\bchat[ _]?id\b", r"\b(user|customer)[ _]?id\b.{0,20}\b(of|for)\b",
    r"\b(dusre|doosre|dusron|kisi aur|kisi or|sabke|sab ke)\b.{0,30}\b(order|data|details|account|address|number|purchase|history)",
]

SENSITIVE_PATTERNS = [
    r"\b(am i|do i have|guess|infer|predict|figure out|deduce|work out|based on my|from my|judging by my)\b.{0,40}\b(pregnan\w*|religio\w*|caste|hindu|muslim|christian|sikh|jain|gay|lesbian|sexual\w*|politic\w*|vote|disease|ill(ness)?|diabet\w*|depress\w*|hiv|cancer|mental health|health|salary|income|debt|rich|poor)\b",
    r"\bwhat (is|'s) my (religion|caste|sexuality|political|politics|health|income|salary)\b",
    r"\b(which|what) (religion|caste|party)\b.{0,20}\b(am i|do i|i (am|belong|follow|support))\b",
    r"\b(kya (main|mai|me)|meri|mera)\b.{0,25}\b(pregnant|religion|dharm|jaati|jati|caste|bimari|beemari)\b",
]

SELF_HARM_PATTERNS = [r"\b(kill myself|suicide|end my life|want to die|self[- ]harm|khudkushi|marna chahta|marna chahti)\b"]
HARMFUL_PATTERNS = [
    r"\b(bomb|explosives?|detonator|poison (someone|him|her)|make (a )?weapon|meth|cocaine|heroin|ransomware|malware|phishing|keylogger)\b",
    r"\bhack(ing)?\b.{0,20}\b(account|website|server|wifi|phone|password)s?\b",
]

TOXIC_WORDS = (
    "fuck", "fucking", "fucker", "motherfucker", "bitch", "bastard", "asshole", "dickhead", "cunt", "slut", "whore",
    "chutiya", "chutiye", "madarchod", "maderchod", "bhenchod", "behenchod", "bhosdike", "bhosadi", "gandu",
    "harami", "randi", "kutte", "kamina", "kamine",
)
TOXIC_PHRASES = (r"\bf[\W_]*u?[\W_]*c[\W_]*k(ing|er)?\b",r"\bi('ll| will) kill you\b", r"\b(stupid|useless|dumb|idiot) (bot|machine|ai)\b", r"\bgo die\b")

# Also checked on letters-only text ('f.u.c.k'); only long, unambiguous words, to avoid false hits.
_TOXIC_SQUASHED = ("fuck", "chutiya", "madarchod", "bhenchod", "behenchod", "bhosdike")

OFF_TOPIC_PATTERNS = [
    r"\b(write|compose|draft|create)\b.{0,25}\b(poem|essay|story|song|lyrics|code|script|program|function|letter|email|speech|article|joke|assignment)\b",
    r"\b(python|javascript|java|sql|html|css|c\+\+|regex)\b.{0,20}\b(code|program|script|function|query|bug)\b",
    r"\bcapital of\b", r"\bweather\b", r"\b(latest )?news\b", r"\bwho (won|is the|was the|invented|discovered)\b",
    r"\b(prime minister|president|chief minister|election|parliament)\b", r"\b(cricket|match|ipl) score\b",
    r"\bhomework\b", r"\bsolve\b.{0,20}\b(equation|problem|this|for x)\b", r"\b\d+\s*[\+\-\*/x]\s*\d+\s*=?\s*\?",
    r"\btranslate\b", r"\btell me a joke\b", r"\bjoke\b", r"\b(recipe|how to cook)\b", r"\b(symptoms?|diagnos\w*|medical advice|which medicine|dosage)\b",
    r"\b(legal advice|lawyer|court case)\b", r"\b(stock market|share price|crypto|bitcoin|invest(ing|ment)?|mutual funds?|trading tips)\b",
    r"\b(horoscope|astrology|kundli)\b", r"\b(meaning of life|girlfriend|boyfriend|relationship advice|dating)\b",
    r"\b(movie|film|web series) (review|recommendation|suggestion)s?\b", r"\bwho (are you|made you|created you|built you)\b",
]

# Clearly about news / results, even when they contain a product word ("cricket", "football").
NEWS_PATTERNS = [
    r"\bwho won\b", r"\b(latest )?news\b", r"\b(election|prime minister|chief minister|parliament)\b",
    r"\b(cricket|football|ipl|match|game) (score|result)s?\b",
    r"\b(match|game|final)\b.{0,15}\b(yesterday|today|tonight|last night)\b",
]

SHOPPING_WORDS = {
    "buy", "buying", "purchase", "price", "prices", "cost", "cheap", "cheaper", "cheapest", "budget",
    "rupees", "inr", "recommend", "recommendation", "recommendations", "suggest", "suggestion", "product",
    "products", "item", "items", "brand", "brands", "order", "orders", "cart", "offer", "offers", "coupon", "coupons",
    "discount", "discounts", "deal", "deals", "sale", "gift", "gifts", "compare", "comparison", "similar", "review",
    "reviews", "rating", "ratings", "delivery", "return", "returns", "size", "colour", "color", "shop",
    "shopping", "store", "kharid", "kharidna", "kharidni",
    "chahiye", "dikhao", "sasta", "saste", "mehenga", "daam", "kimat", "keemat", "lena", "wishlist", "book", "books",
}

# ---------------------------------------------------------------------------
# PII masking (personal data is masked BEFORE anything goes to Claude or the logs)
# ---------------------------------------------------------------------------

PII_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("email", "[EMAIL]", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("upi", "[UPI]", re.compile(r"\b[\w.-]+@(okaxis|oksbi|okhdfcbank|okicici|ybl|paytm|upi|ibl|axl|apl)\b", re.I)),
    ("card", "[CARD]", re.compile(r"\b(?:\d[ -]?){12,18}\d\b")),
    ("aadhaar", "[AADHAAR]", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}\b")),
    ("phone", "[PHONE]", re.compile(r"(?:\+?91[ -]?)?\b[6-9]\d{4}[ -]?\d{5}\b")),
    ("pan", "[PAN]", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
]


def mask_pii(text: str) -> tuple[str, list[str]]:
    """Replace phone numbers, emails, card / Aadhaar / PAN numbers and UPI IDs with tags.

    Returns (masked_text, kinds_found). Order matters: cards (13–19 digits) are masked
    before Aadhaar (12 digits) so a card number is never half-masked.
    """
    found: list[str] = []
    for kind, tag, pattern in PII_PATTERNS:
        text, n = pattern.subn(tag, text)
        if n:
            found.append(kind)
    return text, found


# ---------------------------------------------------------------------------
# Input guardrail
# ---------------------------------------------------------------------------

_recent_messages: dict[str, deque] = defaultdict(deque)


def _rate_limited(user_id: str) -> bool:
    """True if this user sent more than RATE_LIMIT_MESSAGES in the last RATE_LIMIT_WINDOW_S."""
    now = time.monotonic()
    times = _recent_messages[user_id]
    while times and now - times[0] > RATE_LIMIT_WINDOW_S:
        times.popleft()
    times.append(now)
    return len(times) > RATE_LIMIT_MESSAGES


def reset_rate_limits() -> None:
    _recent_messages.clear()


def _first_match(patterns, texts: list[str]) -> str | None:
    for text in texts:
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return m.group(0)[:60]
    return None


# Words in product titles that say nothing about shopping intent.
_GENERIC_WORDS = {"with", "pack", "from", "classic", "premium", "story", "home", "free", "life", "small", "things",
                  "money", "world", "love", "time", "best", "good", "plus", "party", "play", "wings", "fire"}


@lru_cache(maxsize=1)
def _catalog_words() -> frozenset[str]:
    """Words from product titles + category names: 'earbuds', 'atta', 'sneakers'..."""
    words: set[str] = set()
    products = catalog.products()
    cats = catalog._table("categories")
    # Book titles ("The God of Small Things") are full of everyday words, so skip them.
    book_cats = {"C007", *catalog.child_categories("C007")} if len(cats) else set()
    titles = products[~products["category_id"].isin(book_cats)]["title"].astype(str).tolist() if len(products) else []
    names = cats["name"].astype(str).tolist() if len(cats) else []
    for text in titles + names:
        words.update(w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 4 and w not in _GENERIC_WORDS)
    # Common shopping nouns the small sample catalog may not contain.
    words.update({"earphones", "earphone", "headphones", "headphone", "earbuds", "phone", "phones", "mobile",
                  "laptop", "watch", "shoes", "shirt", "jeans", "saree", "kurta", "bag", "charger", "cover",
                  "toy", "toys", "game", "games", "tea", "coffee", "oil", "shampoo", "cream", "mixer", "kettle"})
    return frozenset(words)


def has_shopping_intent(text: str, catalog_words: bool = True) -> bool:
    """catalog_words=False: only explicit shopping words / prices count, not product nouns."""
    norm = normalize(text)
    if re.search(r"₹|\brs\.?\s?\d|\bp\d{5}\b|\b(under|below|within|neeche|andar)\s?\d{3,}", norm):
        return True
    words = set(re.findall(r"[a-z]+", norm))
    return bool(words & SHOPPING_WORDS) or (catalog_words and bool(words & _catalog_words()))


def _blocked(code: str, detail: str) -> GuardrailResult:
    return GuardrailResult(allowed=False, reason=f"{code}: {detail}")


def reason_code(result: GuardrailResult) -> str:
    """'prompt_injection: matched ...' -> 'prompt_injection'."""
    return result.reason.split(":", 1)[0].strip() if result.reason else ""


def check_input(text: str, user_id: str | None = None) -> GuardrailResult:
    """Decide whether a user message may go to the chat agent.

    Checks (in order): empty, length, rate limit (needs user_id), toxicity, self-harm,
    harmful requests, prompt injection, fake-discount requests, other users' data,
    sensitive inferences, off-topic. PII is not a reason to block — it is masked
    by mask_pii() before Claude sees the text.
    """
    if not text or not text.strip():
        return _blocked("empty", "empty message")
    if len(text) > MAX_INPUT_CHARS:
        return _blocked("too_long", f"{len(text)} chars > {MAX_INPUT_CHARS}")
    if user_id and _rate_limited(user_id):
        return _blocked("rate_limited", f"more than {RATE_LIMIT_MESSAGES} messages in {RATE_LIMIT_WINDOW_S}s")

    texts = _variants(text)
    words = set(re.findall(r"[a-z]+", " ".join(texts)))
    toxic = sorted(words & set(TOXIC_WORDS))
    squashed = _squashed(text)
    if toxic or _first_match(TOXIC_PHRASES, texts) or any(w in squashed for w in _TOXIC_SQUASHED):
        return _blocked("toxicity", "abusive language")
    if hit := _first_match(SELF_HARM_PATTERNS, texts):
        return _blocked("self_harm", hit)
    if hit := _first_match(HARMFUL_PATTERNS, texts):
        return _blocked("harmful", hit)

    if hit := (_first_match(INJECTION_PATTERNS, texts) or next((s for s in _INJECTION_SQUASHED if s in squashed), None)):
        return _blocked("prompt_injection", hit)
    if hit := _first_match(DISCOUNT_PATTERNS, texts):
        return _blocked("discount_manipulation", hit)

    # Asking about another user ID (U00002 while logged in as U00001) is never allowed.
    other_ids = {u.upper() for u in re.findall(r"\bu\d{5}\b", texts[0])} - {(user_id or "").upper()}
    if other_ids:
        return _blocked("other_user_data", f"mentions {sorted(other_ids)}")
    if hit := _first_match(OTHER_USER_PATTERNS, texts):
        return _blocked("other_user_data", hit)
    if hit := _first_match(SENSITIVE_PATTERNS, texts):
        return _blocked("sensitive_inference", hit)

    # News / results questions: a product word alone ("cricket", now that we sell cricket bats)
    # doesn't make them shopping questions, only explicit shopping words or a price do.
    if (hit := _first_match(NEWS_PATTERNS, texts)) and not has_shopping_intent(text, catalog_words=False):
        return _blocked("off_topic", hit)
    if (hit := _first_match(OFF_TOPIC_PATTERNS, texts)) and not has_shopping_intent(text):
        return _blocked("off_topic", hit)
    return GuardrailResult(allowed=True, reason="ok")


# ---------------------------------------------------------------------------
# Output guardrail (grounding: everything Claude says must match the DB)
# ---------------------------------------------------------------------------

_MONEY = re.compile(r"(?:₹|\brs\.?|\binr)\s?(\d[\d,]*(?:\.\d+)?)", re.I)
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s?(?:%|percent\b)", re.I)
_RATING = re.compile(r"(?:★\s?(\d(?:\.\d)?))|(?:(\d(?:\.\d)?)\s?(?:★|stars?\b|/\s?5\b))", re.I)
_CODE_AFTER_WORD = re.compile(r"\b(?:code|coupon|promo)\b[:\s\"'“]+([A-Z0-9]{4,20})\b")
_CODE_LIKE = re.compile(r"\b[A-Z]{3,}\d{1,4}\b")


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}


def validate_output(
    reply: ChatReply,
    user_id: str | None = None,
    user_message: str = "",
    allowed_codes: set[str] | None = None,
) -> GuardrailResult:
    """Check a chat reply before it is sent.

    - every product ID (in product_ids and in the text) exists in the catalog
    - no age-restricted products for minors
    - no leaked system prompt, no other user's ID
    - every ₹ amount / % / rating in the text matches the DB (or a number the user typed)
    - every coupon code mentioned is a real offer code (optionally: one of `allowed_codes`)
    """
    text = reply.text or ""
    ids = list(dict.fromkeys(reply.product_ids + re.findall(r"\bP\d{5}\b", text)))
    unknown = [pid for pid in ids if not catalog.product_exists(pid)]
    if unknown:
        return _blocked("unknown_product", f"not in catalog: {unknown}")
    if catalog.is_minor(user_id):
        restricted = [pid for pid in ids if catalog.is_age_restricted(pid)]
        if restricted:
            return _blocked("age_restricted", f"age-restricted items for a minor: {restricted}")

    low = text.lower()
    leaked = [m for m in LEAK_MARKERS if m.lower() in low]
    if leaked:
        return _blocked("prompt_leak", f"reply contains system prompt text: {leaked[0]!r}")
    other_users = set(re.findall(r"\bU\d{5}\b", text)) - {user_id}
    if other_users:
        return _blocked("other_user_data", f"reply mentions {sorted(other_users)}")

    products = [catalog.get_product(pid) for pid in ids]
    user_nums = _numbers(user_message)
    prices = {float(p["price"]) for p in products} | {float(p["mrp"]) for p in products}
    offer_nums = catalog.all_offer_values() | _offer_min_carts()
    for m in _MONEY.finditer(text):
        amount = float(m.group(1).replace(",", ""))
        if amount not in prices | user_nums | offer_nums:
            return _blocked("ungrounded_price", f"₹{m.group(1)} does not match the catalog")

    discounts = {round((float(p["mrp"]) - float(p["price"])) / float(p["mrp"]) * 100) for p in products if float(p["mrp"] or 0) > 0}
    for m in _PERCENT.finditer(text):
        pct = float(m.group(1))
        if pct not in catalog.all_offer_values() | user_nums | discounts or pct > settings.MAX_DISCOUNT_PCT:
            return _blocked("ungrounded_discount", f"{m.group(1)}% does not match any real offer")

    ratings = {round(float(p["avg_rating"]), 1) for p in products}
    for m in _RATING.finditer(text):
        value = float(m.group(1) or m.group(2))
        if round(value, 1) not in ratings and value not in user_nums:
            return _blocked("ungrounded_rating", f"rating {value} does not match the catalog")

    real_codes = catalog.all_offer_codes() if allowed_codes is None else {c.upper() for c in allowed_codes}
    title_words = {w.upper() for p in products for w in re.findall(r"\w+", str(p["title"]))}
    candidates = set(_CODE_AFTER_WORD.findall(text)) | set(_CODE_LIKE.findall(text))
    fake = sorted(c for c in candidates if c.upper() not in real_codes and c.upper() not in title_words
                  and not re.fullmatch(r"P\d{5}|U\d{5}|OF\d{4}", c))
    if fake:
        return _blocked("fake_code", f"unknown offer code(s): {fake}")
    return GuardrailResult(allowed=True, reason="ok")


def _offer_min_carts() -> set[float]:
    offers = catalog._table("offers")
    return set(offers["min_cart_value"].astype(float)) if len(offers) else set()
