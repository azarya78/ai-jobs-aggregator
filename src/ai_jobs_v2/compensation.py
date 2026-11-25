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
    r"(?P<v1>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?P<k1>[kK])?"
    r"(?:\s*(?:-|–|—|to)\s*"
    r"(?P<c2>US\$|\$|€|£|USD|EUR|GBP|IDR|Rp)?\s*"
    r"(?P<v2>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)(?P<k2>[kK])?)?"
    r"\s*(?P<c3>USD|EUR|GBP|IDR)?"
    r"(?:\s*(?:/|per\s+)?\s*(?P<unit>hour|hr|h|task|item|prompt|project|one-time|one\s+time|month|mo|year|yr|annum))?",
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
    u = (unit_raw or "").strip().lower()
    ctx = context_text.lower()
    if u in ("hour", "hr", "h") or any(tok in ctx for tok in ("/hr", "/hour", "per hour", "/h ", "hourly")):
        return "hourly"
    if u in ("task", "item", "prompt") or any(tok in ctx for tok in ("/task", "per task", "per prompt", "per item")):
        return "per_task"
    if u in ("project", "one-time", "one time") or any(
        tok in ctx for tok in ("per project", "/project", "(one-time)", "one-time")
    ):
        return "per_project"
    if u in ("month", "mo") or any(tok in ctx for tok in ("/month", "/mo", "per month", "monthly")):
        return "monthly"
    if u in ("year", "yr", "annum") or any(tok in ctx for tok in ("/year", "/yr", "per year", "annual", "salary range")):
        return "annual"
    return None


def parse_compensation(raw_comp: str, fallback_text: str = "") -> CompensationInfo:
    """
    Extract compensation amount and unit without fabricating missing values
    or inferring hourly rates from fixed-task/project pay.
    """
    source_text = (raw_comp or "").strip()
    if not source_text and fallback_text:
        pay_line_match = re.search(
            r"(?:pay|rate|earn|compensation|salary(?:\s+range)?)\s*[:\-]?\s*([^\n.;]{3,65})",
            fallback_text,
            re.IGNORECASE,
        )
        if pay_line_match:
            candidate = pay_line_match.group(1).strip()
            if re.search(r"(\$|€|£|USD|EUR|GBP|IDR|Rp)\s*\d|\d+\s*(\$|USD|/hr|/hour|/h\b|per hour|per task)", candidate, re.IGNORECASE):
                source_text = candidate

    if not source_text:
        return CompensationInfo()

    has_currency = bool(re.search(r"(\$|€|£|\bUSD\b|\bEUR\b|\bGBP\b|\bIDR\b|\bRp\b)", source_text, re.IGNORECASE))
    has_unit = bool(
        re.search(r"(/hr|/hour|/h\b|per\s+hour|/task|per\s+task|per\s+project|one-time|/month|/mo)", source_text, re.IGNORECASE)
    )
    if not (has_currency or has_unit):
        return CompensationInfo()

    for m in RANGE_PATTERN.finditer(source_text):
        c_raw = m.group("c1") or m.group("c2") or m.group("c3") or ""
        unit_raw = m.group("unit")
        if not c_raw and not unit_raw and not has_currency:
            continue

        v1 = _to_float(m.group("v1"), m.group("k1"))
        v2 = _to_float(m.group("v2"), m.group("k2"))
        if v1 is None:
            continue

        currency = CURRENCY_MAP.get(c_raw.lower(), "USD" if "$" in source_text else None)
        unit = _normalize_unit(unit_raw, source_text)

        if unit is None and v1 >= 50000 and currency in ("USD", "EUR", "GBP"):
            unit = "annual"

        is_up_to = bool(m.group("prefix")) or "up to" in source_text.lower()
        if v2 is not None:
            amt_min, amt_max = min(v1, v2), max(v1, v2)
        elif is_up_to:
            amt_min, amt_max = None, v1
        else:
            amt_min, amt_max = v1, v1

        clean_raw = re.sub(r"\s+", " ", source_text).strip()
        if len(clean_raw) > 64:
            clean_raw = re.sub(r"\s+", " ", m.group(0)).strip()

        return CompensationInfo(
            amount_min=amt_min,
            amount_max=amt_max,
            currency=currency,
            unit=unit,
            raw_text=clean_raw,
        )

    return CompensationInfo()
