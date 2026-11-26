"""Experimental public-API job discovery connectors inspired by Career-Ops.

Upstream Reference & Attribution Notice:
    Provider endpoint, cursor-pagination, HTML-entity decoding, and zero-auth
    transport patterns are adapted in Python from `santifer/career-ops`
    (`career-ops-hq/career-ops`, `providers/himalayas.mjs`,
    `providers/remotive.mjs`, and `providers/jobicy.mjs`):
        Copyright (c) 2026 Santiago Fernández de Valderrama
        Licensed under the MIT License (https://github.com/santifer/career-ops)

Safety Guarantees:
    - Uses official, documented, unauthenticated public JSON endpoints only.
    - Enforces strict HTTPS host allowlists and credential-free URL checks.
    - Never bypasses authentication, CAPTCHAs, or bot challenges.
    - Never fabricates publication dates, geographic eligibility, or pay units.
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple, Type
from urllib.parse import urlparse

from ..http_client import HTTPResponseData, PoliteHTTPClient
from ..models import RawJobListing
from .base import BaseConnector

logger = logging.getLogger(__name__)


def build_https_endpoint(host_parts: List[str], path: str) -> str:
    host = ".".join(part.strip(".") for part in host_parts if part and part.strip("."))
    clean_path = "/" + path.lstrip("/")
    return "https://" + host + clean_path


def validate_allowed_https_url(raw_url: Any, allowed_root_domain: str) -> str:
    if not isinstance(raw_url, str):
        return ""
    candidate = raw_url.strip()
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    if parsed.scheme.lower() != "https":
        return ""
    if "@" in parsed.netloc or any(ch.isspace() for ch in parsed.netloc):
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    root = allowed_root_domain.lower().rstrip(".")
    if host != root and not host.endswith("." + root):
        return ""
    return candidate


def validate_safe_https_logo(raw_url: Any) -> str:
    if not isinstance(raw_url, str):
        return ""
    candidate = raw_url.strip()
    if not candidate:
        return ""
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https" or not host or "." not in host or "@" in parsed.netloc:
        return ""
    return candidate


def epoch_seconds_to_iso(raw_ts: Any) -> Optional[str]:
    if isinstance(raw_ts, bool) or raw_ts is None:
        return None
    val: Optional[float] = None
    if isinstance(raw_ts, (int, float)):
        val = float(raw_ts)
    elif isinstance(raw_ts, str) and re.fullmatch(r"\d+(?:\.\d+)?", raw_ts.strip()):
        val = float(raw_ts.strip())
    if val is None or val <= 946684800:
        return None
    if val > 1e11:
        val = val / 1000.0
    return datetime.fromtimestamp(val, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_remotive_iso_utc(raw_date: Any) -> Optional[str]:
    if not isinstance(raw_date, str):
        return None
    s = raw_date.strip()
    if not s:
        return None
    has_tz = bool(re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", s, re.IGNORECASE))
    candidate = s if has_tz else (s.replace(" ", "T") + "Z" if "T" in s or " " in s else s)
    try:
        dt = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def format_structured_salary(
    min_val: Any,
    max_val: Any,
    currency_raw: Any,
    period_raw: Any,
) -> Tuple[str, Dict[str, Any]]:
    """Build non-fabricating structured salary metadata from provider numeric fields."""
    s_min = float(min_val) if isinstance(min_val, (int, float)) and not isinstance(min_val, bool) and min_val > 0 else None
    s_max = float(max_val) if isinstance(max_val, (int, float)) and not isinstance(max_val, bool) and max_val > 0 else None
    if s_min is None and s_max is None:
        return "", {}

    if s_min is not None and s_max is not None and s_min > s_max:
        s_min, s_max = s_max, s_min

    currency = str(currency_raw).strip().upper() if isinstance(currency_raw, str) and currency_raw.strip() else None
    period_clean = str(period_raw).strip().lower() if isinstance(period_raw, str) and period_raw.strip() else ""
    unit_map = {
        "hourly": "hourly",
        "hour": "hourly",
        "hr": "hourly",
        "monthly": "monthly",
        "month": "monthly",
        "annual": "annual",
        "yearly": "annual",
        "year": "annual",
        "per_task": "per_task",
        "task": "per_task",
        "per_project": "per_project",
        "project": "per_project",
    }
    normalized_unit = unit_map.get(period_clean)

    if currency is None and normalized_unit is None and not period_clean:
        return "", {}

    def _fmt(num: float) -> str:
        return str(int(num)) if num == int(num) else f"{num:.2f}".rstrip("0").rstrip(".")

    if s_min is not None and s_max is not None and s_min != s_max:
        amt_str = f"{_fmt(s_min)} - {_fmt(s_max)}"
    else:
        single = s_max if s_max is not None else s_min
        amt_str = _fmt(single) if single is not None else ""

    prefix = f"{currency} " if currency else ""
    suffix_label = normalized_unit or period_clean
    unit_display_map = {
        "hourly": " / hour",
        "monthly": " / month",
        "annual": " / year",
        "per_task": " / task",
        "per_project": " / project",
        "weekly": " / week",
        "fortnightly": " / fortnight",
    }
    suffix = unit_display_map.get(suffix_label, f" ({suffix_label})" if suffix_label else "")
    raw_display = f"{prefix}{amt_str}{suffix}".strip()

    meta = {
        "min": s_min if s_min is not None else s_max,
        "max": s_max if s_max is not None else s_min,
        "currency": currency,
        "unit": normalized_unit,
        "raw": raw_display or None,
    }
    return raw_display, meta


def _with_base_connector_defaults(
    config: Dict[str, Any],
    default_id: str,
    default_platform: str,
    default_host_parts: List[str],
    default_path: str,
) -> Dict[str, Any]:
    cfg = dict(config)
    cfg.setdefault("id", default_id)
    cfg.setdefault("platform", default_platform)
    host_parts = cfg.get("hostParts") or default_host_parts
    browse_path = str(cfg.get("browsePath") or default_path)
    cfg.setdefault("official_url", str(cfg.get("endpointUrl") or build_https_endpoint(list(host_parts), browse_path)))
    return cfg


class HimalayasDiscoveryConnector(BaseConnector):
    """Python adaptation of Career-Ops `providers/himalayas.mjs` + documented `/jobs/api/search`."""

    DEFAULT_MAX_PAGES = 10
    MAX_PAGES_CAP = 50
    MAX_PAGE_SIZE = 20

    def __init__(self, config: Dict[str, Any], http_client: PoliteHTTPClient):
        super().__init__(
            _with_base_connector_defaults(config, "pilot_himalayas", "Himalayas", ["himalayas", "app"], "/jobs/api"),
            http_client,
        )
        self.last_fetch_metadata: Dict[str, Any] = {}

    def _root_domain(self) -> str:
        parts = self.config.get("hostParts") or ["himalayas", "app"]
        return ".".join(parts)

    def _browse_url(self) -> str:
        if self.config.get("endpointUrl"):
            return str(self.config["endpointUrl"])
        parts = self.config.get("hostParts") or ["himalayas", "app"]
        return build_https_endpoint(parts, self.config.get("browsePath", "/jobs/api"))

    def _search_url(self) -> str:
        if self.config.get("searchEndpointUrl"):
            return str(self.config["searchEndpointUrl"])
        parts = self.config.get("hostParts") or ["himalayas", "app"]
        return build_https_endpoint(parts, self.config.get("searchPath", "/jobs/api/search"))

    def _extract_job_id(self, guid_url: str, apply_url: str, title: str) -> str:
        for candidate in (guid_url, apply_url):
            if candidate:
                path = urlparse(candidate).path.strip("/")
                m = re.search(r"companies/([^/]+)/jobs/([^/?#]+)", path, re.IGNORECASE)
                if m:
                    return f"{m.group(1).lower()}-{m.group(2).lower()}"
                if path:
                    return re.sub(r"[^a-zA-Z0-9._-]+", "-", path)[-96:].strip("-")
        return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:64]

    def _map_item(
        self,
        item: Dict[str, Any],
        source_id: str,
        platform: str,
        root_domain: str,
        now_epoch: float,
    ) -> Tuple[Optional[RawJobListing], bool]:
        if not isinstance(item, dict):
            return None, False

        expiry_raw = item.get("expiryDate")
        if isinstance(expiry_raw, (int, float)) and not isinstance(expiry_raw, bool) and expiry_raw > 0:
            if float(expiry_raw) < now_epoch:
                return None, True

        title = html.unescape(str(item.get("title") or "")).strip()
        if not title:
            return None, False

        guid_url = validate_allowed_https_url(item.get("guid"), root_domain)
        apply_link = validate_allowed_https_url(item.get("applicationLink"), root_domain)
        official_url = guid_url or apply_link
        final_apply_url = apply_link or guid_url
        if not official_url or not final_apply_url:
            return None, False

        company = html.unescape(str(item.get("companyName") or "")).strip() or "Unknown Employer"
        loc_restrictions_raw = item.get("locationRestrictions")
        loc_restrictions: List[str] = []
        if isinstance(loc_restrictions_raw, list):
            loc_restrictions = [
                html.unescape(str(entry)).strip()
                for entry in loc_restrictions_raw
                if isinstance(entry, str) and entry.strip()
            ]

        is_generic_worldwide = len(loc_restrictions) == 0
        location_label = (
            ", ".join(loc_restrictions)
            if loc_restrictions
            else "Remote (Worldwide - unspecified on Himalayas)"
        )

        categories = [
            html.unescape(str(c)).strip()
            for c in (item.get("categories") or [])
            if isinstance(c, str) and c.strip()
        ]
        parent_categories = [
            html.unescape(str(c)).strip()
            for c in (item.get("parentCategories") or [])
            if isinstance(c, str) and c.strip()
        ]
        department = ", ".join(dict.fromkeys(parent_categories + categories))
        employment_type = str(item.get("employmentType") or "").strip()
        posted_iso = epoch_seconds_to_iso(item.get("pubDate"))

        comp_raw, structured_comp = format_structured_salary(
            item.get("minSalary"),
            item.get("maxSalary"),
            item.get("currency"),
            item.get("salaryPeriod"),
        )

        excerpt = str(item.get("excerpt") or "").strip()
        desc_html = str(item.get("description") or "").strip()
        combined_desc = f"{excerpt}\n{desc_html}".strip() if excerpt and desc_html else (desc_html or excerpt)
        job_id = self._extract_job_id(guid_url, final_apply_url, title)

        raw_listing = RawJobListing(
            source_connector=source_id,
            platform=company,
            source_job_id=job_id,
            title=title,
            official_source_url=official_url,
            raw_apply_url=final_apply_url,
            location_raw=location_label,
            description_text=combined_desc,
            compensation_raw=comp_raw,
            official_posted_at=posted_iso,
            employment_type=employment_type,
            department=department,
            logo_url=validate_safe_https_logo(item.get("companyLogo")),
            pure_ai_eval_board=False,
            is_explicitly_closed=False,
            extra_metadata={
                "provider_key": "himalayas",
                "provider_platform": platform,
                "company_name": company,
                "company_slug": str(item.get("companySlug") or "").strip(),
                "location_restrictions": loc_restrictions,
                "timezone_restrictions": item.get("timezoneRestrictions") if isinstance(item.get("timezoneRestrictions"), list) else [],
                "categories": categories,
                "parent_categories": parent_categories,
                "aggregator_generic_worldwide": is_generic_worldwide,
                "preserve_exact_apply_url": True,
                "structured_compensation": structured_comp,
                "source_attribution": {
                    "provider": "Himalayas",
                    "listingUrl": official_url,
                    "requiresLinkBack": True,
                    "requiresUnchangedApplicationLink": True,
                },
            },
        )
        return raw_listing, False

    def fetch_listings(self) -> List[RawJobListing]:
        source_id = str(self.config.get("id") or "pilot_himalayas")
        platform = str(self.config.get("platform") or "Himalayas")
        root_domain = self._root_domain()
        browse_url = self._browse_url()
        page_size = min(max(int(self.config.get("pageSize") or self.MAX_PAGE_SIZE), 1), self.MAX_PAGE_SIZE)
        max_pages = min(max(int(self.config.get("maxPages") or self.DEFAULT_MAX_PAGES), 1), self.MAX_PAGES_CAP)
        rps = float(self.config.get("rateLimitRps") or 2.0)
        now_epoch = float(self.config.get("referenceEpochSec") or time.time())

        results: List[RawJobListing] = []
        seen_ids: Set[str] = set()
        seen_cursors: Set[str] = set()
        cursor: Optional[str] = None
        pages_fetched = 0
        expired_skipped = 0
        truncated = False
        stop_reason = "complete"

        while pages_fetched < max_pages:
            params: Dict[str, Any] = {"limit": page_size}
            if cursor:
                params["cursor"] = cursor

            resp: HTTPResponseData = self.http_client.get(
                browse_url,
                params=params,
                timeout_sec=25,
                rate_limit_rps=rps,
                use_conditional_cache=False,
            )
            if resp.status_code != 200:
                if pages_fetched == 0:
                    raise RuntimeError(f"Himalayas browse endpoint returned HTTP {resp.status_code} for {browse_url}")
                truncated = True
                stop_reason = f"http_{resp.status_code}_on_page_{pages_fetched + 1}"
                logger.warning("Himalayas pagination stopped early on page %d: HTTP %d", pages_fetched + 1, resp.status_code)
                break

            payload = resp.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                if pages_fetched == 0:
                    raise RuntimeError("Himalayas browse endpoint returned invalid JSON schema (missing 'jobs' list)")
                truncated = True
                stop_reason = "invalid_page_schema"
                break

            pages_fetched += 1
            rows = payload["jobs"]
            if not rows:
                stop_reason = "empty_page"
                break

            for item in rows:
                mapped, was_expired = self._map_item(item, source_id, platform, root_domain, now_epoch)
                if was_expired:
                    expired_skipped += 1
                    continue
                if mapped is not None and mapped.source_job_id not in seen_ids:
                    seen_ids.add(mapped.source_job_id)
                    results.append(mapped)

            next_cursor = payload.get("nextCursor")
            if next_cursor is None or next_cursor == "":
                stop_reason = "complete"
                break
            if not isinstance(next_cursor, str):
                truncated = True
                stop_reason = "non_string_cursor"
                break
            if next_cursor in seen_cursors:
                truncated = True
                stop_reason = "repeated_cursor_guard"
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            if cursor:
                truncated = True
                stop_reason = f"page_cap_{max_pages}"

        targeted_added = 0
        if self.config.get("includeTargetedSearch", False):
            search_url = self._search_url()
            for query_params in self.config.get("targetedSearches") or []:
                if not isinstance(query_params, dict):
                    continue
                q_copy = dict(query_params)
                q_copy.setdefault("page", 1)
                try:
                    s_resp = self.http_client.get(
                        search_url,
                        params=q_copy,
                        timeout_sec=20,
                        rate_limit_rps=rps,
                        use_conditional_cache=False,
                    )
                    if s_resp.status_code != 200:
                        logger.info("Himalayas targeted search skipped (%s): HTTP %d", q_copy, s_resp.status_code)
                        continue
                    s_data = s_resp.json()
                    s_rows = s_data.get("jobs") if isinstance(s_data, dict) else None
                    if not isinstance(s_rows, list):
                        continue
                    for item in s_rows:
                        mapped, was_expired = self._map_item(item, source_id, platform, root_domain, now_epoch)
                        if was_expired:
                            expired_skipped += 1
                            continue
                        if mapped is not None and mapped.source_job_id not in seen_ids:
                            seen_ids.add(mapped.source_job_id)
                            results.append(mapped)
                            targeted_added += 1
                except Exception as exc:
                    logger.info("Himalayas targeted search warning (%s): %s", q_copy, exc)

        self.last_fetch_metadata = {
            "pagesFetched": pages_fetched,
            "truncated": truncated,
            "stopReason": stop_reason,
            "expiredSkipped": expired_skipped,
            "targetedSearchAdded": targeted_added,
        }
        return results


class RemotiveDiscoveryConnector(BaseConnector):
    """Python adaptation of Career-Ops `providers/remotive.mjs`."""

    def __init__(self, config: Dict[str, Any], http_client: PoliteHTTPClient):
        super().__init__(
            _with_base_connector_defaults(config, "pilot_remotive", "Remotive", ["remotive", "com"], "/api/remote-jobs"),
            http_client,
        )
        self.last_fetch_metadata: Dict[str, Any] = {}

    def _root_domain(self) -> str:
        parts = self.config.get("hostParts") or ["remotive", "com"]
        return ".".join(parts)

    def _browse_url(self) -> str:
        if self.config.get("endpointUrl"):
            return str(self.config["endpointUrl"])
        parts = self.config.get("hostParts") or ["remotive", "com"]
        return build_https_endpoint(parts, self.config.get("browsePath", "/api/remote-jobs"))

    def fetch_listings(self) -> List[RawJobListing]:
        source_id = str(self.config.get("id") or "pilot_remotive")
        platform = str(self.config.get("platform") or "Remotive")
        root_domain = self._root_domain()
        browse_url = self._browse_url()
        rps = float(self.config.get("rateLimitRps") or 0.4)

        resp: HTTPResponseData = self.http_client.get(
            browse_url,
            timeout_sec=30,
            rate_limit_rps=rps,
            use_conditional_cache=False,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Remotive API returned HTTP {resp.status_code} for {browse_url}")

        payload = resp.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise RuntimeError("Remotive API returned invalid JSON schema (missing 'jobs' list)")

        legal_notice = str(payload.get("0-legal-notice") or payload.get("00-warning") or "").strip()
        rows = payload["jobs"]
        results: List[RawJobListing] = []
        seen_ids: Set[str] = set()

        for item in rows:
            if not isinstance(item, dict):
                continue
            title = html.unescape(str(item.get("title") or "")).strip()
            if not title:
                continue
            remotive_url = validate_allowed_https_url(item.get("url"), root_domain)
            if not remotive_url:
                continue

            raw_id = item.get("id")
            job_id = str(raw_id).strip() if raw_id is not None and str(raw_id).strip() else re.sub(r"[^a-zA-Z0-9_-]+", "-", urlparse(remotive_url).path.strip("/"))
            if job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            company = html.unescape(str(item.get("company_name") or "")).strip() or "Unknown Employer"
            req_loc = html.unescape(str(item.get("candidate_required_location") or "")).strip()
            is_generic_worldwide = bool(
                not req_loc or re.fullmatch(r"(?:worldwide|anywhere|global|remote)", req_loc, re.IGNORECASE)
            )
            location_label = req_loc or "Remote (unspecified on Remotive)"
            posted_iso = parse_remotive_iso_utc(item.get("publication_date"))
            salary_raw = html.unescape(str(item.get("salary") or "")).strip()
            category = html.unescape(str(item.get("category") or "")).strip()
            tags = [
                html.unescape(str(t)).strip()
                for t in (item.get("tags") or [])
                if isinstance(t, str) and t.strip()
            ]
            job_type = str(item.get("job_type") or "").strip()
            desc_html = str(item.get("description") or "").strip()
            logo = validate_safe_https_logo(item.get("company_logo") or item.get("company_logo_url"))

            results.append(
                RawJobListing(
                    source_connector=source_id,
                    platform=company,
                    source_job_id=job_id,
                    title=title,
                    official_source_url=remotive_url,
                    raw_apply_url=remotive_url,
                    location_raw=location_label,
                    description_text=desc_html,
                    compensation_raw=salary_raw,
                    official_posted_at=posted_iso,
                    employment_type=job_type,
                    department=category,
                    logo_url=logo,
                    pure_ai_eval_board=False,
                    is_explicitly_closed=False,
                    extra_metadata={
                        "provider_key": "remotive",
                        "provider_platform": platform,
                        "company_name": company,
                        "tags": tags,
                        "aggregator_generic_worldwide": is_generic_worldwide,
                        "feed_delay_hours": 24,
                        "source_attribution": {
                            "provider": "Remotive",
                            "listingUrl": remotive_url,
                            "requiresLinkBack": True,
                            "legalNoticePresent": bool(legal_notice),
                        },
                    },
                )
            )

        self.last_fetch_metadata = {
            "pagesFetched": 1,
            "truncated": False,
            "stopReason": "complete",
            "reportedJobCount": payload.get("job-count", len(rows)),
            "legalNoticePresent": bool(legal_notice),
        }
        return results


class JobicyDiscoveryConnector(BaseConnector):
    """Python adaptation of Career-Ops `providers/jobicy.mjs`."""

    DEFAULT_MAX_PAGES = 20
    MAX_PAGES_CAP = 100
    DEFAULT_PAGE_SIZE = 100

    def __init__(self, config: Dict[str, Any], http_client: PoliteHTTPClient):
        super().__init__(
            _with_base_connector_defaults(config, "pilot_jobicy", "Jobicy", ["jobicy", "com"], "/api/v2/remote-jobs"),
            http_client,
        )
        self.last_fetch_metadata: Dict[str, Any] = {}

    def _root_domain(self) -> str:
        parts = self.config.get("hostParts") or ["jobicy", "com"]
        return ".".join(parts)

    def _browse_url(self) -> str:
        if self.config.get("endpointUrl"):
            return str(self.config["endpointUrl"])
        parts = self.config.get("hostParts") or ["jobicy", "com"]
        return build_https_endpoint(parts, self.config.get("browsePath", "/api/v2/remote-jobs"))

    def fetch_listings(self) -> List[RawJobListing]:
        source_id = str(self.config.get("id") or "pilot_jobicy")
        platform = str(self.config.get("platform") or "Jobicy")
        root_domain = self._root_domain()
        browse_url = self._browse_url()
        page_size = min(max(int(self.config.get("pageSize") or self.DEFAULT_PAGE_SIZE), 1), 100)
        max_pages = min(max(int(self.config.get("maxPages") or self.DEFAULT_MAX_PAGES), 1), self.MAX_PAGES_CAP)
        rps = float(self.config.get("rateLimitRps") or 0.8)

        results: List[RawJobListing] = []
        seen_ids: Set[str] = set()
        seen_urls: Set[str] = set()
        seen_cursors: Set[str] = set()
        cursor: Optional[str] = None
        pages_fetched = 0
        truncated = False
        stop_reason = "complete"
        friendly_notice = ""

        while pages_fetched < max_pages:
            params: Dict[str, Any] = {"count": page_size}
            if self.config.get("geo"):
                params["geo"] = str(self.config["geo"])
            if self.config.get("industry"):
                params["industry"] = str(self.config["industry"])
            if self.config.get("tag"):
                params["tag"] = str(self.config["tag"])
            if cursor:
                params["cursor"] = cursor

            resp: HTTPResponseData = self.http_client.get(
                browse_url,
                params=params,
                timeout_sec=25,
                rate_limit_rps=rps,
                use_conditional_cache=False,
            )
            if resp.status_code != 200:
                if pages_fetched == 0:
                    raise RuntimeError(f"Jobicy API returned HTTP {resp.status_code} for {browse_url}")
                truncated = True
                stop_reason = f"http_{resp.status_code}_on_page_{pages_fetched + 1}"
                logger.warning("Jobicy pagination stopped early on page %d: HTTP %d", pages_fetched + 1, resp.status_code)
                break

            payload = resp.json()
            if not isinstance(payload, dict):
                if pages_fetched == 0:
                    raise RuntimeError("Jobicy API returned non-object JSON payload")
                truncated = True
                stop_reason = "non_object_payload"
                break

            if payload.get("success") is False:
                err_msg = str(payload.get("error") or "Jobicy reported success=false")
                if pages_fetched == 0:
                    raise RuntimeError(f"Jobicy API error: {err_msg}")
                truncated = True
                stop_reason = f"api_error:{err_msg}"
                break

            rows = payload.get("jobs")
            if not isinstance(rows, list):
                if pages_fetched == 0:
                    raise RuntimeError("Jobicy API payload missing 'jobs' list")
                truncated = True
                stop_reason = "missing_jobs_list"
                break

            if not friendly_notice and isinstance(payload.get("friendlyNotice"), str):
                friendly_notice = payload["friendlyNotice"].strip()

            pages_fetched += 1
            if not rows:
                stop_reason = "empty_page"
                break

            added_this_page = 0
            for item in rows:
                if not isinstance(item, dict):
                    continue
                title = html.unescape(str(item.get("jobTitle") or "")).strip()
                if not title:
                    continue
                jobicy_url = validate_allowed_https_url(item.get("url"), root_domain)
                if not jobicy_url or jobicy_url in seen_urls:
                    continue

                raw_id = item.get("id")
                job_id = str(raw_id).strip() if raw_id is not None and str(raw_id).strip() else str(item.get("jobSlug") or "").strip()
                if not job_id:
                    job_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", urlparse(jobicy_url).path.strip("/"))
                if job_id in seen_ids:
                    continue

                seen_ids.add(job_id)
                seen_urls.add(jobicy_url)
                added_this_page += 1

                company = html.unescape(str(item.get("companyName") or "")).strip() or "Unknown Employer"
                job_geo = html.unescape(str(item.get("jobGeo") or "")).strip()
                is_generic_worldwide = bool(
                    not job_geo or re.fullmatch(r"(?:anywhere|worldwide|global|remote)", job_geo, re.IGNORECASE)
                )
                location_label = job_geo or "Anywhere (unspecified on Jobicy)"

                industries_raw = item.get("jobIndustry")
                if isinstance(industries_raw, list):
                    industries = [html.unescape(str(x)).strip() for x in industries_raw if str(x).strip()]
                elif isinstance(industries_raw, str) and industries_raw.strip():
                    industries = [html.unescape(industries_raw).strip()]
                else:
                    industries = []

                job_types_raw = item.get("jobType")
                if isinstance(job_types_raw, list):
                    emp_type = ", ".join(html.unescape(str(x)).strip() for x in job_types_raw if str(x).strip())
                else:
                    emp_type = html.unescape(str(job_types_raw or "")).strip()

                comp_raw, structured_comp = format_structured_salary(
                    item.get("salaryMin"),
                    item.get("salaryMax"),
                    item.get("salaryCurrency"),
                    item.get("salaryPeriod"),
                )

                pub_raw = str(item.get("pubDate") or "").strip() or None
                excerpt = html.unescape(str(item.get("jobExcerpt") or "")).strip()
                desc_html = str(item.get("jobDescription") or "").strip()
                combined_desc = desc_html or excerpt

                results.append(
                    RawJobListing(
                        source_connector=source_id,
                        platform=company,
                        source_job_id=job_id,
                        title=title,
                        official_source_url=jobicy_url,
                        raw_apply_url=jobicy_url,
                        location_raw=location_label,
                        description_text=combined_desc,
                        compensation_raw=comp_raw,
                        official_posted_at=pub_raw,
                        employment_type=emp_type,
                        department=", ".join(industries),
                        logo_url=validate_safe_https_logo(item.get("companyLogo")),
                        pure_ai_eval_board=False,
                        is_explicitly_closed=False,
                        extra_metadata={
                            "provider_key": "jobicy",
                            "provider_platform": platform,
                            "company_name": company,
                            "job_level": html.unescape(str(item.get("jobLevel") or "")).strip(),
                            "industries": industries,
                            "aggregator_generic_worldwide": is_generic_worldwide,
                            "structured_compensation": structured_comp,
                            "rolling_window_days": 7,
                            "source_attribution": {
                                "provider": "Jobicy",
                                "listingUrl": jobicy_url,
                                "requiresLinkBack": True,
                                "friendlyNoticePresent": bool(friendly_notice),
                            },
                        },
                    )
                )

            next_cursor = payload.get("nextCursor")
            has_more = payload.get("hasMore")
            if has_more is False or next_cursor is None or next_cursor == "":
                stop_reason = "complete"
                break
            if not isinstance(next_cursor, str):
                truncated = True
                stop_reason = "non_string_cursor"
                break
            if next_cursor in seen_cursors or added_this_page == 0:
                stop_reason = "cursor_exhausted_or_no_new_items"
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            if cursor:
                truncated = True
                stop_reason = f"page_cap_{max_pages}"

        self.last_fetch_metadata = {
            "pagesFetched": pages_fetched,
            "truncated": truncated,
            "stopReason": stop_reason,
            "friendlyNoticePresent": bool(friendly_notice),
        }
        return results


PILOT_CONNECTOR_REGISTRY: Dict[str, Type[BaseConnector]] = {
    "himalayas_public_api": HimalayasDiscoveryConnector,
    "remotive_public_api": RemotiveDiscoveryConnector,
    "jobicy_public_api": JobicyDiscoveryConnector,
}


def create_pilot_connector(source_cfg: Dict[str, Any], http_client: PoliteHTTPClient) -> BaseConnector:
    connector_type = str(source_cfg.get("connector") or "")
    cls = PILOT_CONNECTOR_REGISTRY.get(connector_type)
    if cls is None:
        raise ValueError(f"Unknown experimental pilot connector type: {connector_type!r}")
    return cls(source_cfg, http_client)
