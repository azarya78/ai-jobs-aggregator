"""Strict, non-fabricating compensation parser."""
from __future__ import annotations
import re
from typing import Optional
from .models import CompensationInfo

CURRENCY_MAP = {
    "$": "USD",
    "usd": "USD",
    "us$": "USD",
    "€": "EUR",
    "eur": "EUR",
    "£": "GBP",
    "gbp": "GBP",
    "idr": "IDR",
    "rp": "IDR",
}

RANGE_PATTERN = re.compile(
    r"(?P<prefix>up\s+to\s+)?"
    r"(?P<c1>US\$|\$|€|£|USD|EUR|GBP|IDR|Rp)?\s*"
    r"(?P<v1>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<k1>[kK])?"
    r"(?:\s*(?:-|–|to)\s*"
    r"(?P<c2>US\$|\$|€|£|USD|EUR|GBP|IDR|Rp)?\s*"
    r"(?P<v2>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?P<k2>[kK])?)?"
    r"\s*(?P<c3>USD|EUR|GBP|IDR)?"
    r"(?:\s*(?:/|per[\s\-]+)?\s*(?P<unit>hour(?:\s*-?\s*wage)?|hr|h|jam|task|approved\s+asset|approved\s+task|unit|item|prompt|session|project|one-time|one\s+time|month(?:\s*-?\s*salary)?|mo|year(?:\s*-?\s*salary)?|yr|annum))?",
    re.IGNORECASE,
)


def _to_float(val_str: Optional[str], k_suffix: Optional[str]) -> Optional[float]:
    if not val_str:
        return None
    val = float(val_str.replace(",", ""))
    if k_suffix:
        val *= 1000.0
    return val


def _normalize_unit(unit_raw: Optional[str], context_text: str) -> Optional[str]:
    u = re.sub(r"[\s\-]+", " ", (unit_raw or "").strip().lower())
    ctx = context_text.lower()
    if u in ("hour", "hour wage", "hr", "h", "jam") or any(
        tok in ctx for tok in ("/hr", "/hour", "per hour", "per-hour-wage", "per jam", "/h ", "hourly")
    ):
        return "hourly"
    if u in ("task", "item", "prompt", "approved asset", "approved task", "unit") or any(
        tok in ctx
        for tok in ("/task", "per task", "per prompt", "per item", "per approved asset", "per approved task", "per unit")
    ):
        return "per_task"
    if u in ("project", "session", "one time") or any(
        tok in ctx for tok in ("per project", "/project", "per session", "(one-time)", "one-time")
    ):
        return "per_project"
    if u in ("month", "month salary", "mo") or any(
        tok in ctx for tok in ("/month", "/mo", "per month", "per-month-salary", "monthly")
    ):
        return "monthly"
    if u in ("year", "year salary", "yr", "annum") or any(
        tok in ctx for tok in ("/year", "/yr", "per year", "per-year-salary", "annual", "salary range")
    ):
        return "annual"
    return None


def parse_compensation(raw_comp: str, fallback_text: str = "") -> CompensationInfo:
    """
    Extract compensation amount and unit without fabricating missing values
    or inferring hourly rates from fixed-task/project pay.
    """
    used_fallback = False
    source_text = (raw_comp or "").strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", source_text):
        amount = float(source_text)
        return CompensationInfo(amount_min=amount, amount_max=amount, raw_text=source_text)
    if not source_text and fallback_text:
        pay_line_match = re.search(
            r"\b(?:pay|rate|earn|compensation|salary(?:\s+range)?|tarif[^\n.;]{0,40}?rata-rata)\s*[:\-]?\s*([^\n;]{3,65})",
            fallback_text,
            re.IGNORECASE,
        )
        if pay_line_match:
            candidate = pay_line_match.group(1).strip()
            if re.search(
                r"(\$|€|£|USD|EUR|GBP|IDR|Rp)\s*\$?\s*\d|\d+\s*(\$|USD|/hr|/hour|/h\b|per hour|per jam|per task)",
                candidate,
                re.IGNORECASE,
            ):
                source_text = candidate
                used_fallback = True

    if not source_text:
        unit_only = re.search(
            r"\b(?:paid|fixed rate) per (?:approved (?:asset|task)|task)\b",
            fallback_text, re.I,
        )
        if unit_only:
            return CompensationInfo(unit="per_task", raw_text=unit_only.group())
        return CompensationInfo()

    # Normalize comma decimals in explicit currency phrases like "USD $7,50 per jam" (not thousands like "$1,600")
    normalized_source = re.sub(r"(\d+),(\d{1,2})\b(?!\d)", r"\1.\2", source_text)

    has_currency = bool(re.search(r"(\$|€|£|\bUSD\b|\bEUR\b|\bGBP\b|\bIDR\b|\bRp\b)", normalized_source, re.IGNORECASE))
    has_unit = bool(
        re.search(
            r"(/hr|/hour|/h\b|per[\s\-]+hour|per\s+jam|/task|per\s+task|per\s+approved\s+\w+|per\s+session|per\s+project|one-time|/month|/mo)",
            normalized_source,
            re.IGNORECASE,
        )
    )
    if not (has_currency or has_unit):
        return CompensationInfo()

    # Handle "USD $7.50" double currency prefix cleanly
    normalized_for_match = re.sub(r"\bUSD\s+\$", "$", normalized_source, flags=re.IGNORECASE)

    for m in RANGE_PATTERN.finditer(normalized_for_match):
        c_raw = m.group("c1") or m.group("c2") or m.group("c3") or ""
        unit_raw = m.group("unit")
        if not c_raw and not unit_raw:
            continue

        v1 = _to_float(m.group("v1"), m.group("k1"))
        v2 = _to_float(m.group("v2"), m.group("k2"))
        if v1 is None:
            continue

        currency = CURRENCY_MAP.get(c_raw.lower(), "USD" if ("$" in normalized_source or "usd" in normalized_source.lower()) else None)
        unit = _normalize_unit(unit_raw, normalized_source)


        is_up_to = bool(m.group("prefix")) or "up to" in normalized_source.lower()
        if v2 is not None:
            amt_min, amt_max = min(v1, v2), max(v1, v2)
        elif is_up_to:
            amt_min, amt_max = None, v1
        else:
            amt_min, amt_max = v1, v1

        if used_fallback or len(source_text) > 56:
            clean_raw = re.sub(r"\s+", " ", m.group(0)).strip()
        else:
            clean_raw = re.sub(r"\s+", " ", source_text).strip()
            clean_raw = re.sub(r"\bper-hour-wage\b", "hour", clean_raw, flags=re.IGNORECASE)

        return CompensationInfo(
            amount_min=amt_min,
            amount_max=amt_max,
            currency=currency,
            unit=unit,
            raw_text=clean_raw,
        )

    return CompensationInfo()
