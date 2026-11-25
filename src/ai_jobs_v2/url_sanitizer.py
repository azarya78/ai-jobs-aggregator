"""Official Apply URL validation, referral stripping, and anti-aggregator guard."""
from __future__ import annotations
from typing import Tuple
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

BLOCKED_REFERRAL_DOMAINS = {
    "remowork.life",
    "www.remowork.life",
    "aitraining.jobs",
    "www.aitraining.jobs",
    "aitrainer.jobs",
    "www.aitrainer.jobs",
    "humanswanted.io",
    "www.humanswanted.io",
    "alignlist.com",
    "www.alignlist.com",
    "trabajo.org",
    "us.trabajo.org",
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "lnkd.in",
    "rebrand.ly",
}

STRIP_QUERY_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "referring_user",
    "ref",
    "referral",
    "referral_code",
    "referralcode",
    "referrer",
    "via",
    "aff",
    "affiliate",
    "fbclid",
    "gclid",
    "msclkid",
    "twclid",
    "li_fat_id",
    "mc_cid",
    "mc_eid",
    "_hsenc",
    "_hsmi",
    "trk",
}


def sanitize_official_apply_url(raw_url: str, fallback_official_url: str = "") -> Tuple[str, bool, str]:
    """
    Validate and clean an application URL.
    Returns: (clean_url, is_available, diagnostic_note)
    Never fabricates a URL if none is valid.
    """
    candidates = [u.strip() for u in (raw_url, fallback_official_url) if u and u.strip()]
    if not candidates:
        return "", False, "missing_url"

    last_reason = "invalid_url"
    for candidate in candidates:
        try:
            parsed = urlparse(candidate)
            port = parsed.port
        except ValueError:
            last_reason = "malformed_url"
            continue
        if parsed.username is not None or parsed.password is not None or any(c.isspace() for c in parsed.netloc):
            last_reason = "invalid_authority"
            continue
        scheme = parsed.scheme.lower()
        if scheme not in ("https", "http"):
            last_reason = f"unsupported_scheme:{scheme or 'none'}"
            continue

        host = (parsed.hostname or "").lower()
        if not host or "." not in host:
            last_reason = "invalid_host"
            continue

        if host in BLOCKED_REFERRAL_DOMAINS or any(host.endswith("." + d) for d in BLOCKED_REFERRAL_DOMAINS):
            last_reason = f"blocked_aggregator_or_shortener:{host}"
            continue

        normalized_scheme = "https"

        kept_params = []
        for k, v in parse_qsl(parsed.query, keep_blank_values=False):
            if k.lower() in STRIP_QUERY_PARAMS or k.lower().startswith("utm_"):
                continue
            kept_params.append((k, v))

        clean_query = urlencode(kept_params, doseq=True)
        clean_path = parsed.path or "/"
        while "//" in clean_path:
            clean_path = clean_path.replace("//", "/")

        netloc = host
        if port and port not in (80, 443):
            netloc = f"{host}:{port}"

        cleaned = urlunparse((normalized_scheme, netloc, clean_path, "", clean_query, ""))
        return cleaned, True, "ok"

    return "", False, last_reason
