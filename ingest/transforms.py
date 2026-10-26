"""Small converters that turn messy raw values into Standard Format values.

Every transform takes a pandas Series (one raw column) plus options from the mapping
file, and returns a cleaned Series. They never crash on bad values — a value that can't be
converted becomes empty (None) and the row is counted/dropped later by run.py.

    unix_ms / unix_s / date  -> ISO-8601 local time ("2026-08-12T19:30:00")
    money                    -> float ₹ ("₹1,299" -> 1299.0, "$12.99" -> 12.99 × usd_to_inr)
    rating                   -> 1-5 scale (e.g. 8/10 -> 4.2)
    event_map                -> our event_type enum ("addtocart" -> "add_to_cart")
    list_to_path, split_path -> "Clothing > Men > T-shirts"
    join_text, join_pipe     -> one text / "a|b|c"
    strip_html               -> plain text, max 2,000 characters
"""

from __future__ import annotations

import html
import json
import re
from typing import Any, Callable

import pandas as pd

from common.config import settings

MAX_TEXT = 2000
PATH_SEP = " > "
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_SPACE_PUNCT = re.compile(r"\s+([.,!?;:])")     # "great !" -> "great!" (left over from removed tags)


def _is_missing(x: Any) -> bool:
    if x is None:
        return True
    if isinstance(x, float) and x != x:   # NaN
        return True
    return isinstance(x, str) and x.strip() == ""


def _as_list(x: Any) -> list:
    """Raw list-ish values: a real list, a JSON list in a string, or one plain value."""
    if _is_missing(x):
        return []
    if isinstance(x, (list, tuple)):
        return [v for v in x if not _is_missing(v)]
    if hasattr(x, "tolist"):                                   # numpy array from jsonl
        return [v for v in x.tolist() if not _is_missing(v)]
    if isinstance(x, str) and x.strip().startswith("["):
        try:
            return [v for v in json.loads(x) if not _is_missing(v)]
        except (ValueError, TypeError):
            pass
    return [x]


# ---------------------------------------------------------------------------- time
def _to_local_iso(stamps: pd.Series) -> pd.Series:
    """Datetime Series (tz-aware or naive) -> ISO text in TIMEZONE without an offset."""
    if getattr(stamps.dt, "tz", None) is not None:
        stamps = stamps.dt.tz_convert(settings.TIMEZONE).dt.tz_localize(None)
    out = stamps.dt.strftime("%Y-%m-%dT%H:%M:%S").astype(object)
    return out.where(stamps.notna(), None)


def unix_ms(s: pd.Series, **_: Any) -> pd.Series:
    """Unix milliseconds (UTC) -> local ISO."""
    nums = pd.to_numeric(s, errors="coerce")
    return _to_local_iso(pd.to_datetime(nums, unit="ms", utc=True, errors="coerce"))


def unix_s(s: pd.Series, **_: Any) -> pd.Series:
    """Unix seconds (UTC) -> local ISO."""
    nums = pd.to_numeric(s, errors="coerce")
    return _to_local_iso(pd.to_datetime(nums, unit="s", utc=True, errors="coerce"))


def date(s: pd.Series, format: str | None = None, dayfirst: bool = True, **_: Any) -> pd.Series:
    """Any date text -> local ISO. Indian data is day-first ("12-08-2026" = 12 August).

    Values with a timezone ("...Z", "+00:00") are converted to TIMEZONE; values without one
    are assumed to already be local time.
    """
    text = s.astype("string").str.strip()
    if format:
        return _to_local_iso(pd.to_datetime(text, format=format, errors="coerce"))
    # An offset only counts right after a time ("...10:00Z", "...10:00:00+05:30"), so the
    # "-2026" at the end of "12-08-2026" is not mistaken for one.
    has_tz = text.str.contains(r"\d\d:\d\d(?::\d\d(?:\.\d+)?)?\s*(?:Z|[+-]\d\d:?\d\d)$", regex=True, na=False)
    out = pd.Series([None] * len(text), index=text.index, dtype=object)
    if has_tz.any():
        aware = pd.to_datetime(text[has_tz], utc=True, errors="coerce", format="mixed")
        out[has_tz] = _to_local_iso(aware)
    if (~has_tz).any():
        iso_like = text[~has_tz].str.match(r"^\d{4}-\d\d-\d\d", na=False)
        naive = pd.Series(pd.NaT, index=text[~has_tz].index, dtype="datetime64[ns]")
        if iso_like.any():
            naive[iso_like] = pd.to_datetime(text[~has_tz][iso_like], errors="coerce", format="mixed")
        if (~iso_like).any():
            naive[~iso_like] = pd.to_datetime(text[~has_tz][~iso_like], errors="coerce", format="mixed",
                                              dayfirst=dayfirst)
        out[~has_tz] = _to_local_iso(naive)
    return out.where(out.notna(), None)   # unreadable dates -> None (pandas turned them into NaN)


# ---------------------------------------------------------------------------- numbers
_CURRENCY = re.compile(r"(₹|rs\.?|inr|\$|usd|us\$)", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def parse_money(x: Any, rate: float = 1.0) -> float | None:
    """'₹1,299' -> 1299.0 · '$12.99' -> 12.99 × rate · '1,299 - 1,499' -> 1299.0 (first price)."""
    if _is_missing(x):
        return None
    if isinstance(x, (int, float)):
        return round(float(x) * rate, 2)
    match = _NUMBER.search(_CURRENCY.sub("", str(x)))
    if not match:
        return None
    return round(float(match.group().replace(",", "")) * rate, 2)


def money(s: pd.Series, rate: float = 1.0, **_: Any) -> pd.Series:
    return s.map(lambda x: parse_money(x, rate))


def rating(s: pd.Series, scale: list[float] | tuple[float, float] = (1, 5), **_: Any) -> pd.Series:
    """Rescale ratings to 1-5. scale = the raw range, e.g. [1, 10] or [0, 5]."""
    lo, hi = float(scale[0]), float(scale[1])
    nums = pd.to_numeric(s, errors="coerce")
    scaled = 1 + (nums - lo) * 4 / (hi - lo) if (lo, hi) != (1.0, 5.0) else nums
    return scaled.clip(1, 5).round(1)


# ---------------------------------------------------------------------------- enums
def event_map(s: pd.Series, mapping: dict[str, str] | None = None, **_: Any) -> pd.Series:
    """Raw event names -> our enum. Unknown names become None (dropped + counted later)."""
    mapping = {str(k).strip().lower(): v for k, v in (mapping or {}).items()}
    values = [None if _is_missing(x) else mapping.get(str(x).strip().lower()) for x in s]
    return pd.Series(values, index=s.index, dtype=object)


# ---------------------------------------------------------------------------- text
def strip_html(x: Any) -> str:
    if _is_missing(x):
        return ""
    text = html.unescape(_TAG.sub(" ", str(x)))
    text = _SPACES.sub(" ", text).strip()
    return _SPACE_PUNCT.sub(lambda m: m.group(1), text)[:MAX_TEXT]


def clean_text(s: pd.Series, **_: Any) -> pd.Series:
    return s.map(strip_html)


def join_text(s: pd.Series, **_: Any) -> pd.Series:
    """A list of sentences -> one cleaned text."""
    return s.map(lambda x: strip_html(" ".join(str(v) for v in _as_list(x))))


def join_pipe(s: pd.Series, **_: Any) -> pd.Series:
    """A list of tags -> 'a|b|c' (pipes inside values become spaces)."""
    return s.map(lambda x: "|".join(_SPACES.sub(" ", strip_html(v).replace("|", " "))[:100] for v in _as_list(x)))


def list_to_path(s: pd.Series, **_: Any) -> pd.Series:
    """['Electronics', 'Mobiles'] -> 'Electronics > Mobiles'."""
    return s.map(lambda x: PATH_SEP.join(strip_html(v) for v in _as_list(x)) or None)


def split_path(s: pd.Series, sep: str = ">", **_: Any) -> pd.Series:
    """'Clothing>Men>T-shirts' (any separator) -> 'Clothing > Men > T-shirts'."""
    def one(x: Any) -> str | None:
        if _is_missing(x):
            return None
        parts = [strip_html(p) for p in str(x).split(sep)]
        return PATH_SEP.join(p for p in parts if p) or None
    return s.map(one)


TRANSFORMS: dict[str, Callable[..., pd.Series]] = {
    "unix_ms": unix_ms, "unix_s": unix_s, "date": date, "money": money, "rating": rating,
    "event_map": event_map, "list_to_path": list_to_path, "split_path": split_path, "join_text": join_text,
    "join_pipe": join_pipe, "strip_html": clean_text,
}
