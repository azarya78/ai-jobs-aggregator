"""Indonesia-centric Geographic Eligibility Classifier with Anti-False-Positive Guards."""
from __future__ import annotations
import re
from typing import Optional
from .models import (
    RawJobListing,
    EligibilityResult,
    ELIGIBILITY_INDONESIA,
    ELIGIBILITY_GLOBAL,
    ELIGIBILITY_RESTRICTED,
    ELIGIBILITY_UNVERIFIED,
)

INDONESIA_LOCATION_PATTERNS = [
    r"\b(?:remote\s*[-–·,|]\s*)?indonesia\b",
    r"\bjakarta\b",
    r"\bsurabaya\b",
    r"\bbandung\b",
    r"\bbali\b",
    r"\bindonesian\s*\(\s*indonesia\s*\)",
    r"\b[-–]\s*indonesia\s*$",
]

INDONESIA_BODY_ELIGIBLE_PATTERNS = [
    r"\b(?:based|residing|living|located|work\s+location|open\s+to\s+applicants|hire[sd]?)\s+in\s+(?:[a-z,\s]+and\s+)?indonesia\b",
    r"\bindonesia[- ]based\b",
    r"\bremote\s+in\s+indonesia\b",
    r"\bincluding\s+indonesia\b",
    r"\bcountries\s*:\s*[^\n.]{0,120}\bindonesia\b",
]

INDONESIA_EXCLUSION_PATTERNS = [
    r"\b(?:excluding|except|not\s+available\s+in|not\s+open\s+to\s+residents\s+of)\s+[^\n.]{0,60}\bindonesia\b",
]

GLOBAL_ELIGIBLE_PATTERNS = [
    r"\baccepts?\s+applicants?\s+(?:worldwide|globally)\b",
    r"\bopen\s+(?:worldwide|globally|to\s+all\s+countries|to\s+every\s+country)\b",
    r"\bremote\s*[-–(]?\s*(?:worldwide|global)\b",
    r"\bwork\s+from\s+anywhere\s+in\s+the\s+world\b",
    r"\bacross\s+1[5-9]\d\+?\s+countries\b",
    r"\bfreelancers\s+from\s+all\s+over\s+the\s+world\b",
]

REGIONAL_AMBIGUOUS_PATTERNS = [
    r"\bapac\b",
    r"\basia[- ]pacific\b",
    r"\bsoutheast\s+asia\b",
    r"\basean\b",
    r"\basia\b",
]

NON_INDONESIA_LOCATIONS = [
    "united states", "usa", "u.s.", "u.s. residents", "us only", "us/can", "canada",
    "united kingdom", "uk only", "great britain", "london", "ireland", "germany",
    "france", "spain", "italy", "netherlands", "belgium", "sweden", "norway",
    "denmark", "finland", "poland", "hungary", "croatia", "czech", "romania",
    "bulgaria", "austria", "switzerland", "greece", "portugal", "slovenia", "malta",
    "australia", "new zealand", "japan", "south korea", "korea", "singapore",
    "malaysia", "vietnam", "thailand", "philippines", "taiwan", "hong kong",
    "india", "delhi", "mumbai", "bangalore", "hyderabad", "odisha", "pakistan",
    "bangladesh", "israel", "turkey", "saudi arabia", "uae", "mexico", "brazil",
    "argentina", "colombia", "chile", "peru", "latam", "south africa", "kenya",
    "nigeria", "egypt", "nyc", "new york", "san francisco", "los angeles",
    "mountain view", "chatswood", "dallas", "houston", "miami", "acworth", "herat",
]

BODY_RESTRICTION_CLAUSES = [
    r"(?:based\s+in\s+one\s+of\s+the\s+following\s+countries|open\s+to\s+residents\s+of|must\s+(?:reside|live|be\s+based|be\s+located)\s+in|currently\s+based\s+in|work\s+location\s*:\s*remote\s+in|owners\s+in\s+the)\s*[:\-]?\s*([^\n.]{3,160})",
    r"\(\s*((?:us|u\.s\.|uk|canada|mexico|eu|australia|india)[a-z/\s,&]*only)\s*\)",
    r"\b((?:us|u\.s\.|uk|mexico|canada|us/can)\s+(?:only|based|residents\s+only))\b",
    r"\bin[- ]person\s+research\s+study\b",
    r"\bon[- ]site\s+data\s+collection\b",
    r"\bfull[- ]time\s+us\s+employees\b",
]


def _detect_indonesian_language(title: str, desc: str) -> bool:
    combined = f"{title}\n{desc}"
    return bool(re.search(r"\b(indonesian|bahasa\s+indonesia)\b", combined, re.IGNORECASE))


def _contains_indonesia(text: str) -> bool:
    return bool(re.search(r"\bindonesia(?:n\s*\(\s*indonesia\s*\))?\b|\bjakarta\b", text, re.IGNORECASE))


def _find_non_id_location_mention(text: str) -> Optional[str]:
    low = text.lower()
    for place in NON_INDONESIA_LOCATIONS:
        if re.search(r"\b" + re.escape(place) + r"\b", low):
            return place
    stripped = text.strip()
    if stripped in {"US", "UK", "GB", "CA", "AU", "DE", "FR", "SE", "NO", "DK", "FI", "NL", "ES", "IT", "JP", "KR", "IN", "BR", "MX", "VN", "SG", "MY", "PH", "TH"}:
        return stripped
    return None


def classify_eligibility(raw: RawJobListing) -> EligibilityResult:
    title = (raw.title or "").strip()
    loc = (raw.location_raw or "").strip()
    desc = (raw.description_text or "").strip()
    combined = f"{title}\n{loc}\n{desc}"

    is_id_lang = _detect_indonesian_language(title, desc)

    for pat in INDONESIA_EXCLUSION_PATTERNS:
        m = re.search(pat, combined, re.IGNORECASE)
        if m:
            return EligibilityResult(
                classification=ELIGIBILITY_RESTRICTED,
                confidence="high",
                evidence=f"Explicit Indonesia exclusion: '{m.group(0).strip()}'",
                is_indonesian_language=is_id_lang,
            )

    loc_has_indonesia = _contains_indonesia(loc)
    body_has_indonesia_residency = any(
        re.search(pat, desc, re.IGNORECASE) for pat in INDONESIA_BODY_ELIGIBLE_PATTERNS
    )

    if loc and not loc_has_indonesia and not body_has_indonesia_residency:
        non_id_loc = _find_non_id_location_mention(loc)
        if non_id_loc:
            return EligibilityResult(
                classification=ELIGIBILITY_RESTRICTED,
                confidence="high",
                evidence=f"Location restricted outside Indonesia: '{loc}' (matched '{non_id_loc}')",
                is_indonesian_language=is_id_lang,
            )

    title_loc_check = re.sub(r"\bindonesian\s*\(\s*indonesia\s*\)", "", title, flags=re.IGNORECASE)
    if not loc_has_indonesia and not body_has_indonesia_residency:
        title_restricted_match = re.search(
            r"(?:\(\s*([^)]+)\s*\)|\bin\s+(nyc|new\s+york|los\s+angeles|london|sweden|vietnam|india|united\s+states|canada|uk|australia)\b)",
            title_loc_check,
            re.IGNORECASE,
        )
        if title_restricted_match:
            qual = title_restricted_match.group(0)
            non_id_in_title = _find_non_id_location_mention(qual)
            if non_id_in_title and not _contains_indonesia(qual):
                return EligibilityResult(
                    classification=ELIGIBILITY_RESTRICTED,
                    confidence="high",
                    evidence=f"Title restricts geography to '{qual.strip()}'",
                    is_indonesian_language=is_id_lang,
                )

    for pat in BODY_RESTRICTION_CLAUSES:
        for m in re.finditer(pat, desc, re.IGNORECASE):
            clause = re.sub(r"\s+", " ", m.group(0)).strip()
            if not _contains_indonesia(clause):
                if _find_non_id_location_mention(clause) or re.search(r"\b(in[- ]person|on[- ]site|us\s+employees)\b", clause, re.IGNORECASE):
                    return EligibilityResult(
                        classification=ELIGIBILITY_RESTRICTED,
                        confidence="high",
                        evidence=f"Description restricts eligibility: '{clause[:140]}'",
                        is_indonesian_language=is_id_lang,
                    )

    for pat in INDONESIA_LOCATION_PATTERNS:
        m = re.search(pat, f"{loc}\n{title}", re.IGNORECASE | re.MULTILINE)
        if m:
            return EligibilityResult(
                classification=ELIGIBILITY_INDONESIA,
                confidence="high",
                evidence=f"Explicit Indonesia location metadata/title: '{m.group(0).strip()}' (Location: '{loc or 'N/A'}')",
                is_indonesian_language=True,
            )

    for pat in INDONESIA_BODY_ELIGIBLE_PATTERNS:
        m = re.search(pat, desc, re.IGNORECASE)
        if m:
            matched_id_clause = re.sub(r"\s+", " ", m.group(0)).strip()[:140]
            return EligibilityResult(
                classification=ELIGIBILITY_INDONESIA,
                confidence="high",
                evidence=f"Description confirms Indonesia residency eligibility: '{matched_id_clause}'",
                is_indonesian_language=True,
            )

    for rpat in REGIONAL_AMBIGUOUS_PATTERNS:
        rm = re.search(rpat, f"{loc}\n{desc}", re.IGNORECASE)
        if rm:
            return EligibilityResult(
                classification=ELIGIBILITY_UNVERIFIED,
                confidence="medium",
                evidence=f"Regional scope '{rm.group(0)}' mentioned without explicit Indonesia confirmation (Location: '{loc or 'Unspecified'}')",
                is_indonesian_language=is_id_lang,
            )

    has_us_tax_lock = bool(re.search(r"\b(w-2\s+only|us\s+work\s+authorization\s+required|must\s+be\s+authorized\s+to\s+work\s+in\s+the\s+u\.?s\.?)\b", desc, re.IGNORECASE))
    if not has_us_tax_lock:
        for gpat in GLOBAL_ELIGIBLE_PATTERNS:
            gm = re.search(gpat, f"{loc}\n{desc}", re.IGNORECASE)
            if gm:
                matched_global_clause = re.sub(r"\s+", " ", gm.group(0)).strip()
                return EligibilityResult(
                    classification=ELIGIBILITY_GLOBAL,
                    confidence="high",
                    evidence=f"Verified global remote clause: '{matched_global_clause}'",
                    is_indonesian_language=is_id_lang,
                )

    if is_id_lang:
        return EligibilityResult(
            classification=ELIGIBILITY_UNVERIFIED,
            confidence="medium",
            evidence=f"Targets Indonesian language but residency country is not explicitly confirmed (Location: '{loc or 'Unspecified'}')",
            is_indonesian_language=True,
        )

    return EligibilityResult(
        classification=ELIGIBILITY_UNVERIFIED,
        confidence="low",
        evidence=f"Insufficient geographic eligibility evidence (Location: '{loc or 'Unspecified'}'; generic remote cannot be assumed global)",
        is_indonesian_language=False,
    )
