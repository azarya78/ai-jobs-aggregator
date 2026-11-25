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
    r"\b(?:remote\s*[-\u2013\u2014·,|/]+\s*)?indonesia\b",
    r"\bjakarta\b",
    r"\bsurabaya\b",
    r"\bbandung\b",
    r"\bbali\b",
    r"\byogyakarta\b",
    r"\bmedan\b",
    r"\bindonesian\s*\(\s*indonesia\s*\)",
    r"\b[-\u2013\u2014]\s*indonesia\s*$",
    r"(?:^|[·|;])\s*ID\s*(?:[·|;]|$)",
]

INDONESIA_BODY_ELIGIBLE_PATTERNS = [
    r"\b(?:based|residing|living|located|work\s+location|open\s+to\s+(?:applicants|experts|candidates|residents)|hire[sd]?)\s+in\s+(?:[a-z,\s]+and\s+)?indonesia\b",
    r"\bresidency\s+in\s+indonesia\b",
    r"\bresidents?\s+of\s+(?:[a-z,\s]+and\s+)?indonesia\b",
    r"\bindonesia[- ]based\b",
    r"\bremote\s+in\s+indonesia\b",
    r"\bincluding\s+indonesia\b",
    r"\bavailable\s+in\s+\d+\s+countries\s+[^\n.]{0,400}\bindonesia\b",
    r"\bopen\s+in\s+\d+\s+countries\s+[^\n.]{0,400}\bindonesia\b",
    r"\bcountries\s*:\s*[^\n.]{0,250}\bindonesia\b",
]

INDONESIA_EXCLUSION_PATTERNS = [
    r"\b(?:excluding|except|not\s+available\s+in|not\s+open\s+to\s+residents\s+of)\s+[^\n.]{0,60}\bindonesia\b",
]

GLOBAL_LOCATION_EXACT_PATTERN = re.compile(
    r"^(?:remote\s*[-\u2013\u2014·,|/()\s]+)?(?:worldwide|global|english\s+global|all\s+countries|anywhere(?:\s+in\s+the\s+world)?)$",
    re.IGNORECASE,
)

GLOBAL_ELIGIBLE_PATTERNS = [
    r"\baccepts?\s+(?:applicants?|candidates?|contributors?|freelancers?)\s+(?:worldwide|globally|from\s+all\s+countries|from\s+anywhere(?:\s+in\s+the\s+world)?)\b",
    r"\bopen\s+(?:worldwide|globally|to\s+all\s+countries|to\s+every\s+country|to\s+(?:applicants?|candidates?|contributors?)\s+worldwide)\b",
    r"\bremote\s*[-\u2013\u2014()]+\s*(?:worldwide|global)\b",
    r"\bwork\s+from\s+anywhere\s+in\s+the\s+world\b",
    r"\bapplicants\s+based\s+elsewhere\s+are\s+welcome\b",
    r"\bno\s+geographic(?:al)?\s+restrictions\b",
]

REGIONAL_AMBIGUOUS_PATTERNS = [
    r"\bapac\b",
    r"\basia[- ]pacific\b",
    r"\bsoutheast\s+asia\b",
    r"\basean\b",
    r"\b(?:remote\s*[-\u2013\u2014·,|]+\s*)?asia\b",
    r"\blatam\b",
    r"\bemea\b",
]

# Comprehensive non-Indonesia countries, territories, states, and major cities
NON_INDONESIA_LOCATIONS = [
    # North America
    "united states", "usa", "u.s.", "u.s. residents", "us only", "us/can", "canada",
    "puerto rico", "mexico", "costa rica", "panama", "guatemala", "dominican republic",
    "el salvador", "honduras", "nicaragua", "jamaica", "cuba", "north america",
    "nyc", "new york", "san francisco", "los angeles", "chicago", "seattle", "boston",
    "austin", "atlanta", "denver", "phoenix", "san jose", "santa clara", "sunnyvale",
    "mountain view", "dallas", "houston", "miami", "las vegas", "tysons", "plano",
    "acworth", "toronto", "vancouver", "montreal", "ottawa", "calgary", "mexico city",
    # Europe
    "united kingdom", "uk only", "great britain", "england", "scotland", "wales",
    "northern ireland", "ireland", "germany", "france", "spain", "italy", "netherlands",
    "belgium", "sweden", "norway", "denmark", "finland", "iceland", "poland", "hungary",
    "croatia", "czech republic", "czechia", "czech", "slovakia", "romania", "bulgaria",
    "austria", "switzerland", "greece", "portugal", "slovenia", "malta", "cyprus",
    "luxembourg", "estonia", "latvia", "lithuania", "ukraine", "belarus", "moldova",
    "serbia", "bosnia", "herzegovina", "albania", "north macedonia", "montenegro",
    "kosovo", "georgia", "armenia", "azerbaijan", "european union", "eu only",
    "london", "dublin", "berlin", "munich", "frankfurt", "paris", "madrid", "barcelona",
    "milan", "rome", "amsterdam", "brussels", "vienna", "zurich", "geneva", "stockholm",
    "oslo", "copenhagen", "helsinki", "reykjavik", "warsaw", "prague", "budapest",
    "bratislava", "ljubljana", "zagreb", "belgrade", "bucharest", "sofia", "athens",
    "lisbon", "kyiv", "tallinn", "riga", "vilnius",
    # Asia-Pacific (outside Indonesia)
    "australia", "new zealand", "japan", "south korea", "korea", "singapore",
    "malaysia", "vietnam", "thailand", "philippines", "taiwan", "hong kong",
    "china", "mainland china", "india", "pakistan", "bangladesh", "sri lanka",
    "nepal", "bhutan", "myanmar", "cambodia", "laos", "brunei", "mongolia",
    "kazakhstan", "uzbekistan", "kyrgyzstan", "tajikistan", "turkmenistan", "afghanistan",
    "delhi", "new delhi", "mumbai", "bangalore", "bengaluru", "hyderabad", "chennai",
    "pune", "kolkata", "ahmedabad", "odisha", "uttarpradesh", "uttar pradesh",
    "gurgaon", "noida", "tokyo", "osaka", "seoul", "taipei", "manila", "bangkok",
    "hanoi", "ho chi minh", "kuala lumpur", "colombo", "dhaka", "karachi", "lahore",
    "kathmandu", "sydney", "melbourne", "brisbane", "perth", "chatswood", "auckland",
    "herat", "almaty", "tashkent",
    # Middle East & Africa
    "israel", "turkey", "saudi arabia", "united arab emirates", "uae", "qatar",
    "kuwait", "bahrain", "oman", "jordan", "lebanon", "palestine", "syria", "iraq",
    "iran", "yemen", "south africa", "kenya", "nigeria", "egypt", "morocco", "tunisia",
    "algeria", "libya", "ghana", "ethiopia", "cameroon", "rwanda", "uganda", "tanzania",
    "zambia", "zimbabwe", "senegal", "dubai", "abu dhabi", "riyadh", "doha", "tel aviv",
    "jerusalem", "amman", "beirut", "istanbul", "ankara", "cairo", "lagos", "nairobi",
    "johannesburg", "cape town", "accra", "addis ababa", "kigali",
    # South America
    "brazil", "argentina", "colombia", "chile", "peru", "venezuela", "ecuador",
    "bolivia", "paraguay", "uruguay", "sao paulo", "buenos aires", "bogota", "lima",
    "santiago",
]

# Non-ID ISO-3166-1 alpha-2 country codes matched ONLY in structured location delimiters
NON_ID_ISO2_CODES = {
    "US", "UK", "GB", "CA", "AU", "NZ", "DE", "FR", "ES", "IT", "NL", "BE", "LU",
    "AT", "CH", "IE", "PT", "SE", "NO", "DK", "FI", "IS", "PL", "CZ", "SK", "HU",
    "RO", "BG", "GR", "HR", "SI", "RS", "BA", "AL", "MK", "ME", "EE", "LV", "LT",
    "UA", "BY", "MD", "GE", "AM", "AZ", "CY", "MT", "TR", "IL", "SA", "AE", "QA",
    "KW", "BH", "OM", "JO", "LB", "PS", "SY", "IQ", "IR", "YE", "EG", "MA", "TN",
    "DZ", "LY", "ZA", "NG", "KE", "GH", "ET", "CM", "RW", "UG", "TZ", "ZM", "ZW",
    "JP", "KR", "CN", "TW", "HK", "MO", "SG", "MY", "VN", "TH", "PH", "MM", "KH",
    "LA", "BN", "IN", "PK", "BD", "LK", "NP", "BT", "MV", "MN", "KZ", "UZ", "KG",
    "TJ", "TM", "AF", "BR", "MX", "AR", "CO", "CL", "PE", "VE", "EC", "BO", "PY",
    "UY", "CR", "PA", "GT", "DO", "SV", "HN", "NI", "JM", "PR", "CU",
}

US_STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DC", "DE", "FL", "GA", "HI", "IA",
    "IL", "KS", "KY", "LA", "MA", "MD", "MI", "MN", "MO", "MS", "MT", "NC", "ND",
    "NE", "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VA", "VT", "WA", "WI", "WV", "WY",
}

BODY_RESTRICTION_CLAUSES = [
    r"(?:based\s+in\s+one\s+of\s+the\s+following\s+countries|open\s+to\s+(?:residents|applicants|experts|candidates)\s+(?:living\s+|residing\s+|based\s+)?(?:of|in)|must\s+(?:reside|live|be\s+based|be\s+located)\s+in|have\s+lived\s+in|reside\s+in|currently\s+based\s+in|work\s+location\s*:\s*remote\s+in|owners\s+in\s+the)\s*[:-]?\s*([^\n.]{3,200})",
    r"\b(?:available|open)\s+in\s+\d+\s+countries\s+([^\n.]{3,400})",
    r"\(\s*(?:us|u\.s\.|uk|canada|mexico|eu|australia|india)[a-z/\s,&]*only\s*\)",
    r"\b((?:us|u\.s\.|uk|mexico|canada|us/can|eu|india|australia)\s+(?:only|based|residents\s+only))\b",
    r"\bin[- ]person\s+(?:research\s+|movement\s+)?study\b",
    r"\bon[- ]site\s+(?:data\s+collection|study)\b",
    r"\bfull[- ]time\s+us\s+employees\b",
]


def _detect_indonesian_language(title: str, desc: str) -> bool:
    combined = f"{title}\n{desc}"
    return bool(re.search(r"\b(indonesian|bahasa\s+indonesia)\b", combined, re.IGNORECASE))


def _contains_indonesia(text: str) -> bool:
    return bool(
        re.search(
            r"\bindonesia(?:\s*\(\s*indonesia\s*\))?\b|\bjakarta\b|(?:^|[·|;])\s*ID\s*(?:[·|;]|$)",
            text,
            re.IGNORECASE,
        )
    )


def _find_structured_iso_lock(loc_text: str) -> Optional[str]:
    """Detect structured ISO-3166-1 alpha-2 or US state tokens in location metadata without false-matching prose words."""
    stripped = loc_text.strip()
    if not stripped:
        return None
    if stripped.upper() in NON_ID_ISO2_CODES and len(stripped) == 2:
        return stripped.upper()

    # Lever format: "Slovakia · SK · remote" or "Kyiv · UA · remote"
    for tok in re.split(r"\s*[·|;/]\s*", stripped):
        t_clean = tok.strip()
        if re.fullmatch(r"[A-Z]{2}", t_clean) and t_clean in NON_ID_ISO2_CODES:
            return t_clean

    # SmartRecruiters / City, State, Country format: "Tysons, VA, us" or "Plano, TX, us"
    comma_parts = [p.strip() for p in stripped.split(",") if p.strip()]
    if len(comma_parts) >= 2:
        last_tok = comma_parts[-1].upper()
        if re.fullmatch(r"[A-Za-z]{2}", comma_parts[-1]) and last_tok in NON_ID_ISO2_CODES:
            return last_tok
        if len(comma_parts) >= 2 and comma_parts[1].upper() in US_STATE_CODES:
            return f"US-{comma_parts[1].upper()}"

    return None


def _find_non_id_location_mention(text: str, check_structured_iso: bool = False) -> Optional[str]:
    low = text.lower()
    for place in NON_INDONESIA_LOCATIONS:
        if re.search(r"\b" + re.escape(place) + r"\b", low):
            return place
    if check_structured_iso:
        iso_hit = _find_structured_iso_lock(text)
        if iso_hit:
            return iso_hit
    else:
        stripped = text.strip()
        if stripped in NON_ID_ISO2_CODES:
            return stripped
    return None


def classify_eligibility(raw: RawJobListing) -> EligibilityResult:
    title = (raw.title or "").strip()
    loc = (raw.location_raw or "").strip()
    desc = (raw.description_text or "").strip()
    combined = f"{title}\n{loc}\n{desc}"

    is_id_lang = _detect_indonesian_language(title, desc)

    # Public Mercor posting metadata separates applicant and residence country lists.
    permitted_country_field = None
    for key in ("ineligibleResidenceLocation", "ineligibleLocation", "eligibleResidenceLocation", "eligibleLocation"):
        values = raw.extra_metadata.get(key)
        if not values or not isinstance(values, list) or not all(isinstance(v, str) and re.fullmatch(r"[A-Z]{3}", v) for v in values):
            continue
        allow_list = key.startswith("eligible")
        if (allow_list and "IDN" not in values) or (not allow_list and "IDN" in values):
            return EligibilityResult(ELIGIBILITY_RESTRICTED, "high", f"{key}: {', '.join(values)}", is_id_lang)
        if allow_list:
            permitted_country_field = key

    for pat in INDONESIA_EXCLUSION_PATTERNS:
        m = re.search(pat, combined, re.IGNORECASE)
        if m:
            return EligibilityResult(
                classification=ELIGIBILITY_RESTRICTED,
                confidence="high",
                evidence=f"Explicit Indonesia exclusion: '{m.group(0).strip()}'",
                is_indonesian_language=is_id_lang,
            )

    legal_lock = re.search(
        r"\b(?:w-2 only|us work authorization required|must be authorized to work in (?:the )?(?:u\.?s\.?|united states)\b)", desc, re.I
    )
    if legal_lock:
        return EligibilityResult(ELIGIBILITY_RESTRICTED, "high", legal_lock.group(), is_id_lang)

    loc_has_indonesia = _contains_indonesia(loc)
    body_has_indonesia_residency = any(
        re.search(pat, desc, re.IGNORECASE) for pat in INDONESIA_BODY_ELIGIBLE_PATTERNS
    )
    body_has_explicit_worldwide = any(
        re.search(pat, desc, re.IGNORECASE) for pat in GLOBAL_ELIGIBLE_PATTERNS
    )

    if loc and not permitted_country_field and not loc_has_indonesia and not body_has_indonesia_residency and not body_has_explicit_worldwide:
        non_id_loc = _find_non_id_location_mention(loc, check_structured_iso=True)
        if non_id_loc:
            return EligibilityResult(
                classification=ELIGIBILITY_RESTRICTED,
                confidence="high",
                evidence=f"Location restricted outside Indonesia: '{loc}' (matched '{non_id_loc}')",
                is_indonesian_language=is_id_lang,
            )

    title_loc_check = re.sub(r"\bindonesian\s*\(\s*indonesia\s*\)", "", title, flags=re.IGNORECASE)
    if not loc_has_indonesia and not body_has_indonesia_residency and not body_has_explicit_worldwide:
        # Check parenthetical qualifiers "(Chicago)", "(EU, UK, US...)", trailing "- Vienna" / "– India", or "in NYC"
        for qual_match in re.finditer(
            r"(?:\(\s*([^)]+)\s*\)|[-\u2013\u2014]\s*([A-Za-z][A-Za-z\s,]{2,35})$|\bin\s+([A-Za-z][A-Za-z\s]{2,25})\b)",
            title_loc_check,
        ):
            qual = qual_match.group(1) or qual_match.group(2) or qual_match.group(3) or ""
            non_id_in_title = _find_non_id_location_mention(qual, check_structured_iso=False)
            if non_id_in_title and not _contains_indonesia(qual):
                return EligibilityResult(
                    classification=ELIGIBILITY_RESTRICTED,
                    confidence="high",
                    evidence=f"Title restricts geography to '{qual.strip()}' (matched '{non_id_in_title}')",
                    is_indonesian_language=is_id_lang,
                )

    for pat in BODY_RESTRICTION_CLAUSES:
        for m in re.finditer(pat, desc, re.IGNORECASE):
            clause = re.sub(r"\s+", " ", m.group(0)).strip()
            if not _contains_indonesia(clause) and not any(
                re.search(gp, clause, re.IGNORECASE) for gp in GLOBAL_ELIGIBLE_PATTERNS
            ):
                if _find_non_id_location_mention(clause, check_structured_iso=False) or re.search(
                    r"\b(in[- ]person|on[- ]site|us\s+employees)\b", clause, re.IGNORECASE
                ):
                    return EligibilityResult(
                        classification=ELIGIBILITY_RESTRICTED,
                        confidence="high",
                        evidence=f"Description restricts eligibility: '{clause[:140]}'",
                        is_indonesian_language=is_id_lang,
                    )

    if permitted_country_field:
        return EligibilityResult(ELIGIBILITY_INDONESIA, "high", f"Indonesia (IDN) listed in {permitted_country_field}", is_id_lang)

    # A broad country list in employer boilerplate must not silently override
    # structured location metadata that points to another country.  Such roles
    # need human review unless a role-specific clause or worldwide permission
    # explicitly establishes eligibility outside that location.
    broad_country_list = bool(re.search(
        r"\bcountries\s*:|\b(?:available|open)\s+in\s+\d+\s+countries\b",
        desc, re.IGNORECASE,
    ))
    explicit_role_indonesia = any(re.search(p, desc, re.IGNORECASE) for p in
        INDONESIA_BODY_ELIGIBLE_PATTERNS[:5])
    if (loc and not loc_has_indonesia and broad_country_list
            and not explicit_role_indonesia and not body_has_explicit_worldwide):
        foreign_location = _find_non_id_location_mention(loc, check_structured_iso=True)
        if foreign_location:
            return EligibilityResult(
                classification=ELIGIBILITY_UNVERIFIED,
                confidence="medium",
                evidence=(
                    f"Conflicting geographic signals: location '{loc}' is outside "
                    "Indonesia while a broad description country list mentions "
                    "Indonesia; role-specific permission is not confirmed"
                ),
                is_indonesian_language=is_id_lang,
            )

    # A language label such as "Bahasa Indonesia" identifies no work location.
    geographic_title = re.sub(r"\bBahasa Indonesia\b", "", title, flags=re.I)
    for pat in INDONESIA_LOCATION_PATTERNS:
        m = re.search(pat, f"{loc}\n{geographic_title}", re.IGNORECASE | re.MULTILINE)
        if m:
            return EligibilityResult(
                classification=ELIGIBILITY_INDONESIA,
                confidence="high",
                evidence=f"Explicit Indonesia location metadata/title: '{m.group(0).strip()}' (Location: '{loc or 'N/A'}')",
                is_indonesian_language=is_id_lang,
            )

    for pat in INDONESIA_BODY_ELIGIBLE_PATTERNS[:5]:
        m = re.search(pat, desc, re.IGNORECASE)
        if m:
            matched_id_clause = re.sub(r"\s+", " ", m.group(0)).strip()[:140]
            return EligibilityResult(
                classification=ELIGIBILITY_INDONESIA,
                confidence="high",
                evidence=f"Description confirms Indonesia residency eligibility: '{matched_id_clause}'",
                is_indonesian_language=is_id_lang,
            )

    approved = re.search(
        r"candidates must be physically located in one of the approved countries[.:\s]+approved countries:\s*([^.!?]+)", desc, re.I
    )
    if approved and _contains_indonesia(approved[1]) and not _find_non_id_location_mention(loc, True):
        return EligibilityResult(ELIGIBILITY_INDONESIA, "high", approved.group()[:350], is_id_lang)

    if loc and GLOBAL_LOCATION_EXACT_PATTERN.match(loc):
        return EligibilityResult(
            classification=ELIGIBILITY_GLOBAL,
            confidence="high",
            evidence=f"Explicit global location metadata: '{loc}'",
            is_indonesian_language=is_id_lang,
        )
    for gpat in GLOBAL_ELIGIBLE_PATTERNS:
        gm = re.search(gpat, desc, re.IGNORECASE)
        if gm:
            matched_global_clause = re.sub(r"\s+", " ", gm.group(0)).strip()
            return EligibilityResult(
                classification=ELIGIBILITY_GLOBAL,
                confidence="high",
                evidence=f"Verified global remote clause: '{matched_global_clause}'",
                is_indonesian_language=is_id_lang,
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
