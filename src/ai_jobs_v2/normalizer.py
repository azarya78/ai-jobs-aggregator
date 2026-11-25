"""Job record normalization, HTML sanitization, and canonical ID builder."""
from __future__ import annotations
import hashlib
import html
import re
from datetime import datetime, timezone
from typing import Optional, Tuple
from html.parser import HTMLParser
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


class _StdlibHTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in ("script", "style", "noscript"):
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in ("script", "style", "noscript") and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts)

from .models import (
    RawJobListing,
    NormalizedJobRecord,
    RelevanceResult,
    EligibilityResult,
    STATUS_NEW,
)
from .compensation import parse_compensation
from .relevance import classify_relevance
from .eligibility import classify_eligibility
from .url_sanitizer import sanitize_official_apply_url


def strip_html_to_text(raw_html_or_text: str) -> str:
    if not raw_html_or_text:
        return ""
    unescaped = html.unescape(raw_html_or_text)
    if "<" in unescaped and ">" in unescaped:
        if BeautifulSoup is not None:
            soup = BeautifulSoup(unescaped, "html.parser")
            for tag in soup(["script", "style", "noscript"]):
                tag.decompose()
            text = soup.get_text(separator=" ")
        else:
            stripper = _StdlibHTMLStripper()
            stripper.feed(unescaped)
            text = stripper.get_text()
    else:
        text = unescaped
    return re.sub(r"\s+", " ", text).strip()


def normalize_iso_date(raw_date: Optional[str]) -> Optional[str]:
    """Normalize official posting timestamp to ISO-8601 UTC without fabricating missing dates."""
    if raw_date is None:
        return None
    s = str(raw_date).strip()
    if not s:
        return None
    if re.fullmatch(r"\d{10,13}", s):
        val = int(s)
        if val > 10_000_000_000:
            val_sec = val / 1000.0
        else:
            val_sec = float(val)
        return datetime.fromtimestamp(val_sec, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        pass

    from email.utils import parsedate_to_datetime
    try:
        dt_rss = parsedate_to_datetime(s)
        if dt_rss.tzinfo is None:
            dt_rss = dt_rss.replace(tzinfo=timezone.utc)
        return dt_rss.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        pass

    for fmt in ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%Y/%m/%d"):
        try:
            dt_p = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            return dt_p.strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue

    return None


def build_canonical_id(source_connector: str, source_job_id: str, clean_apply_url: str, title: str) -> str:
    prefix = re.sub(r"[^a-z0-9_]", "_", source_connector.lower())
    clean_sid = re.sub(r"[^a-zA-Z0-9_\-]", "", (source_job_id or "").strip())
    if clean_sid:
        return f"{prefix}:{clean_sid}"
    basis = clean_apply_url or f"{source_connector}|{title.strip().lower()}"
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def normalize_raw_listing(
    raw: RawJobListing,
    collected_at_iso: str,
) -> Tuple[Optional[NormalizedJobRecord], RelevanceResult, Optional[EligibilityResult]]:
    clean_desc = strip_html_to_text(raw.description_text)
    cleaned_raw = RawJobListing(
        source_connector=raw.source_connector,
        platform=raw.platform,
        source_job_id=raw.source_job_id,
        title=re.sub(r"\s+", " ", (raw.title or "")).strip(),
        official_source_url=raw.official_source_url,
        raw_apply_url=raw.raw_apply_url,
        location_raw=re.sub(r"\s+", " ", (raw.location_raw or "")).strip(),
        description_text=clean_desc,
        compensation_raw=raw.compensation_raw,
        official_posted_at=raw.official_posted_at,
        employment_type=raw.employment_type,
        department=raw.department,
        logo_url=raw.logo_url,
        pure_ai_eval_board=raw.pure_ai_eval_board,
        is_explicitly_closed=raw.is_explicitly_closed,
        extra_metadata=raw.extra_metadata,
    )

    relevance = classify_relevance(cleaned_raw)
    if not relevance.is_relevant:
        return None, relevance, None

    eligibility = classify_eligibility(cleaned_raw)
    comp = parse_compensation(cleaned_raw.compensation_raw, fallback_text=clean_desc)
    clean_apply, apply_ok, _ = sanitize_official_apply_url(
        cleaned_raw.raw_apply_url, fallback_official_url=cleaned_raw.official_source_url
    )
    clean_source_url, _, _ = sanitize_official_apply_url(cleaned_raw.official_source_url)

    canonical_id = build_canonical_id(
        cleaned_raw.source_connector,
        cleaned_raw.source_job_id,
        clean_apply,
        cleaned_raw.title,
    )

    excerpt = clean_desc[:220].rsplit(" ", 1)[0] + "..." if len(clean_desc) > 220 else clean_desc
    if not excerpt:
        excerpt = f"{cleaned_raw.title} at {cleaned_raw.platform}."

    desc_hash = hashlib.sha256(
        f"{cleaned_raw.title}|{cleaned_raw.location_raw}|{comp.raw_text}|{clean_desc}".encode("utf-8")
    ).hexdigest()[:16]

    official_date = normalize_iso_date(cleaned_raw.official_posted_at)

    record = NormalizedJobRecord(
        canonical_id=canonical_id,
        source_job_id=cleaned_raw.source_job_id or canonical_id.split(":", 1)[-1],
        title=cleaned_raw.title,
        company=cleaned_raw.platform,
        logo=cleaned_raw.logo_url,
        official_source_url=clean_source_url or cleaned_raw.official_source_url,
        apply_url=clean_apply,
        apply_url_available=apply_ok,
        category=relevance.category,
        skills=relevance.extracted_skills,
        location=cleaned_raw.location_raw or "Remote",
        eligibility_class=eligibility.classification,
        eligibility_confidence=eligibility.confidence,
        eligibility_evidence=eligibility.evidence,
        is_indonesian=(eligibility.classification == "Indonesia Eligible" or eligibility.is_indonesian_language),
        is_direct=True,
        compensation=comp,
        short_description=excerpt,
        full_description_hash=desc_hash,
        official_posted_date=official_date,
        first_detected_at=collected_at_iso,
        last_verified_at=collected_at_iso,
        job_status=STATUS_NEW,
        source_connector=cleaned_raw.source_connector,
        collected_at=collected_at_iso,
    )
    return record, relevance, eligibility
