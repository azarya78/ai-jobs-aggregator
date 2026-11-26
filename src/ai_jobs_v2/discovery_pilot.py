"""Career-Ops Inspired Experimental Discovery Pilot for AI Jobs Hub v2.1.

Upstream Reference & Attribution Notice:
    Public-provider discovery patterns for Himalayas, Remotive, and Jobicy are
    adapted in Python from `santifer/career-ops` (`career-ops-hq/career-ops`):
        Copyright (c) 2026 Santiago Fernández de Valderrama
        Licensed under the MIT License (https://github.com/santifer/career-ops)

Pilot Isolation & Safety Contract:
    - Reads existing v2 staging & state artifacts (`staging/jobs_feed.json`,
      `state/job_history.json`, `state/unverified_jobs.json`,
      `state/coverage_report.json`) in STRICT READ-ONLY mode.
    - Never modifies `staging/jobs_feed.json`, `jobs_feed.json`, or
      `state/job_history.json`.
    - Never treats generic "Worldwide", "Anywhere", or empty country arrays from
      third-party job boards as proof of Indonesia or verified global eligibility.
    - Keeps verified `Indonesia Eligible` candidates strictly separate from
      `Unverified` candidate discovery in `staging/discovery_candidates.json`.
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import logging
import pathlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from .compensation import parse_compensation
from .connectors.career_ops_providers import create_pilot_connector
from .eligibility import classify_eligibility
from .http_client import PoliteHTTPClient
from .models import (
    ELIGIBILITY_GLOBAL,
    ELIGIBILITY_INDONESIA,
    ELIGIBILITY_RESTRICTED,
    ELIGIBILITY_UNVERIFIED,
    CompensationInfo,
    RawJobListing,
)
from .normalizer import build_canonical_id, normalize_iso_date, strip_html_to_text
from .relevance import classify_relevance
from .url_sanitizer import BLOCKED_REFERRAL_DOMAINS, sanitize_official_apply_url
from .validator import write_json_atomically

logger = logging.getLogger(__name__)

BRAND_ALIAS_GROUPS: List[Tuple[str, Tuple[str, ...]]] = [
    ("welodata", ("welodata", "welo data", "welocalize", "weloglobal")),
    ("oneforma", ("oneforma", "one forma", "centific")),
    ("dataforce", ("dataforce", "data force", "transperfect")),
    ("mindrift", ("mindrift", "toloka")),
    ("appen", ("appen", "crowdgen")),
    ("rws", ("rws", "trainai", "rws trainai", "moravia")),
    ("imerit", ("imerit", "imerit scholars")),
    ("mercor", ("mercor",)),
    ("alignerr", ("alignerr", "labelbox")),
    ("afterquery", ("afterquery", "after query")),
    ("invisible", ("invisible", "invisible technologies", "invisibletech")),
    ("outlier", ("outlier", "scale ai")),
    ("micro1", ("micro1",)),
    ("telus", ("telus", "telus digital", "telus international")),
]

BROAD_AI_TITLE_RULES: List[Tuple[str, str]] = [
    (
        "AI Tutor / AI Trainer",
        r"\b(?:ai\s+(?:tutor|trainer|instructor|educator|coach)|rlhf|red[- ]teaming|prompt\s+(?:writer|author|evaluator))\b",
    ),
    (
        "Data Annotation & AI Quality",
        r"\b(?:data\s+(?:annotat\w*|label\w*|curator)|annotation\s+(?:specialist|analyst)|ai\s+(?:quality|qa|rater|evaluator|reviewer)|search\s+(?:quality|relevance))\b",
    ),
    (
        "Prompt & Context Engineering",
        r"\b(?:prompt\s+engineer\w*|context\s+engineer\w*|ai\s+conversation\s+designer|conversational\s+ai)\b",
    ),
    (
        "LLM & Generative AI Engineering",
        r"\b(?:llm|large\s+language\s+models?|genai|generative\s+ai|agentic\s+ai|ai\s+agents?|rag)\b",
    ),
    (
        "NLP, Speech & Computer Vision",
        r"\b(?:nlp|natural\s+language\s+processing|computational\s+linguist\w*|computer\s+vision|speech\s+recognition|asr|tts|multimodal\s+ai)\b",
    ),
    (
        "Machine Learning & MLOps",
        r"\b(?:machine\s+learning|deep\s+learning|mlops|ml\s+(?:engineer\w*|scientist\w*|research\w*|infra\w*|platform\w*|ops)|ai\s*/\s*ml|ml\s*/\s*ai)\b",
    ),
    (
        "Applied AI & AI Research",
        r"\b(?:applied\s+ai|ai\s+(?:engineer\w*|research\w*|scientist\w*|architect\w*|developer\w*|specialist\w*|tech\s+lead|platform\w*|infrastructure|safety|alignment|data\s+scientist\w*|product\s+engineer\w*|software\s+engineer\w*|full[- ]stack\w*|backend\w*)|(?:software|backend|full[- ]stack|staff|senior|principal|lead|founding|research|data)\s+engineer\w*[\s,\-–—()/]+(?:ai|ml|llm|genai|machine\s+learning)|data\s+scientist\w*[\s,\-–—()/]+(?:ai|ml|llm|nlp))\b",
    ),
]

NON_AI_GTM_ADMIN_TITLE_EXCLUSIONS = re.compile(
    r"\b(?:"
    r"account\s+executive|sales\s+(?:representative|manager|director|development|engineer|ops|operations)|"
    r"business\s+development|bdr\b|sdr\b|partnerships?\s+manager|"
    r"marketing\s+(?:manager|director|specialist|lead|ops)|growth\s+marketer|content\s+(?:marketer|marketing)|"
    r"seo\s+(?:specialist|manager)|social\s+media|public\s+relations|communications\s+manager|"
    r"recruiter|talent\s+acquisition|sourcer|people\s+operations|hr\s+(?:manager|business\s+partner|generalist)|"
    r"customer\s+(?:success|support|service|care)|technical\s+support\s+representative|"
    r"accountant|controller|payroll|tax\s+|legal\s+counsel|general\s+counsel|paralegal|"
    r"executive\s+assistant|administrative\s+assistant|office\s+manager|graphic\s+designer"
    r")\b",
    re.IGNORECASE,
)

FOREIGN_ONLY_GEO_LABEL = re.compile(
    r"^(?:"
    r"usa?(?:\s+only)?|united\s+states(?:\s+only)?|u\.s\.(?:a\.)?(?:\s+only)?|north\s+america(?:\s+only)?|"
    r"uk(?:\s+only)?|united\s+kingdom|canada(?:\s+only)?|europe(?:\s+only)?|eu(?:\s+only)?|emea(?:\s+only)?|"
    r"latam(?:\s+only)?|latin\s+america|americas(?:\s+only)?|germany|france|spain|poland|brazil|mexico|india|"
    r"australia(?:\s+only)?|new\s+zealand|philippines|vietnam|colombia|argentina"
    r")$",
    re.IGNORECASE,
)

REGIONAL_AMBIGUOUS_LABEL = re.compile(
    r"\b(?:apac|asia[- ]?pacific|southeast\s+asia|asean|asia)\b",
    re.IGNORECASE,
)

GENERIC_WORLDWIDE_LABEL = re.compile(
    r"\b(?:worldwide|anywhere|global)\b",
    re.IGNORECASE,
)


def normalize_pilot_posted_date(raw_date: Optional[str]) -> Optional[str]:
    if not raw_date:
        return None
    iso = normalize_iso_date(raw_date)
    if iso:
        return iso
    s = str(raw_date).strip()
    if not s:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return None


def is_posted_within_days(posted_iso: Optional[str], run_iso: str, days: int = 7) -> bool:
    """Strictly check whether officialPostedDate falls within `days` of `run_iso`.

    Never substitutes discoveryDate when `posted_iso` is missing (`None`).
    """
    if not posted_iso:
        return False
    try:
        posted_dt = datetime.fromisoformat(posted_iso.replace("Z", "+00:00"))
        run_dt = datetime.fromisoformat(run_iso.replace("Z", "+00:00"))
        if posted_dt.tzinfo is None:
            posted_dt = posted_dt.replace(tzinfo=timezone.utc)
        if run_dt.tzinfo is None:
            run_dt = run_dt.replace(tzinfo=timezone.utc)
        lower_bound = run_dt - timedelta(days=days)
        upper_bound = run_dt + timedelta(days=1)
        return lower_bound <= posted_dt <= upper_bound
    except ValueError:
        return False


def normalize_company_key(company: str) -> str:
    cleaned = re.sub(r"\([^)]*\)", " ", (company or "").lower())
    cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned).strip()
    full_raw = re.sub(r"[^a-z0-9]+", " ", (company or "").lower()).strip()
    for canonical, aliases in BRAND_ALIAS_GROUPS:
        for alias in aliases:
            if alias in full_raw or alias in cleaned:
                return canonical
    cleaned = re.sub(r"\b(?:inc|llc|ltd|corp|corporation|co|gmbh|pte|tbk|pt|ai\s+inc)\b", "", cleaned).strip()
    return re.sub(r"\s+", " ", cleaned)


def normalize_title_key(title: str) -> str:
    lowered = html_or_plain_lower(title)
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def html_or_plain_lower(text: str) -> str:
    return strip_html_to_text(text or "").lower()


def canonicalize_dedup_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    try:
        parsed = urlparse(raw_url.strip())
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+", "/", (parsed.path or "").rstrip("/"))
    if path.endswith("/apply"):
        path = path[:-6]
    if not host:
        return ""
    return f"{host}{path.lower()}"


def extract_ats_signature(raw_url: str) -> str:
    if not raw_url:
        return ""
    try:
        parsed = urlparse(raw_url.strip())
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    path = (parsed.path or "").strip("/")
    if ".".join(("lever", "co")) in host:
        parts = path.split("/")
        if len(parts) >= 2:
            return f"lever:{parts[0].lower()}:{parts[1].lower()}"
    if ".".join(("greenhouse", "io")) in host:
        m = re.search(r"boards/([^/]+)/jobs/(\d+)", path, re.IGNORECASE)
        if m:
            return f"greenhouse:{m.group(1).lower()}:{m.group(2)}"
        m2 = re.search(r"^([^/]+)/jobs/(\d+)", path, re.IGNORECASE)
        if m2:
            return f"greenhouse:{m2.group(1).lower()}:{m2.group(2)}"
    if ".".join(("ashbyhq", "com")) in host:
        parts = path.split("/")
        if len(parts) >= 2:
            return f"ashby:{parts[0].lower()}:{parts[1].lower()}"
    if ".".join(("workable", "com")) in host:
        m = re.search(r"([^/]+)/j/([A-Za-z0-9]+)", path)
        if m:
            return f"workable:{m.group(1).lower()}:{m.group(2).upper()}"
    return ""


def preserve_exact_https_apply_url(raw_url: str) -> Tuple[str, bool, str]:
    candidate = (raw_url or "").strip()
    if not candidate:
        return "", False, "missing_url"
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return "", False, "malformed_url"
    if parsed.scheme.lower() != "https":
        return "", False, f"unsupported_scheme:{parsed.scheme or 'none'}"
    if "@" in parsed.netloc or any(c.isspace() for c in parsed.netloc):
        return "", False, "invalid_authority"
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        return "", False, "invalid_host"
    if host in BLOCKED_REFERRAL_DOMAINS or any(host.endswith("." + d) for d in BLOCKED_REFERRAL_DOMAINS):
        return "", False, f"blocked_domain:{host}"
    return candidate, True, "ok_unchanged"


@dataclass
class ExistingDirectIndex:
    url_to_job_id: Dict[str, str] = field(default_factory=dict)
    ats_to_job_id: Dict[str, str] = field(default_factory=dict)
    company_title_to_job_id: Dict[Tuple[str, str], str] = field(default_factory=dict)
    active_feed_count: int = 0
    active_indonesia_count: int = 0
    active_global_count: int = 0
    active_within_7d_count: int = 0
    relevant_within_7d_count: int = 0
    history_tracked_count: int = 0
    unverified_direct_count: int = 0
    raw_retrieved_count: int = 0
    relevant_direct_count: int = 0
    sources_configured: int = 0
    sources_succeeded: int = 0
    sources_failed: int = 0

    def register_record(
        self,
        job_id: str,
        company: str,
        title: str,
        apply_url: str,
        official_source_url: str,
    ) -> None:
        if not job_id:
            return
        for u in (apply_url, official_source_url):
            cu = canonicalize_dedup_url(u)
            if cu and cu not in self.url_to_job_id:
                self.url_to_job_id[cu] = job_id
            ats = extract_ats_signature(u)
            if ats and ats not in self.ats_to_job_id:
                self.ats_to_job_id[ats] = job_id
        ck = normalize_company_key(company)
        tk = normalize_title_key(title)
        if ck and tk and (ck, tk) not in self.company_title_to_job_id:
            self.company_title_to_job_id[(ck, tk)] = job_id

    def match(
        self,
        company: str,
        title: str,
        apply_url: str,
        original_source_url: str,
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        for u in (apply_url, original_source_url):
            cu = canonicalize_dedup_url(u)
            if cu and cu in self.url_to_job_id:
                return True, self.url_to_job_id[cu], "canonical_url"
            ats = extract_ats_signature(u)
            if ats and ats in self.ats_to_job_id:
                return True, self.ats_to_job_id[ats], "ats_posting_id"
        ck = normalize_company_key(company)
        tk = normalize_title_key(title)
        if ck and tk and (ck, tk) in self.company_title_to_job_id:
            return True, self.company_title_to_job_id[(ck, tk)], "company_and_title"
        return False, None, None


def load_existing_direct_index(
    staging_feed_path: pathlib.Path,
    state_dir: pathlib.Path,
    run_iso: str,
) -> ExistingDirectIndex:
    idx = ExistingDirectIndex()
    relevant_7d_seen_ids: Set[str] = set()

    if staging_feed_path.exists():
        feed_data = json.loads(staging_feed_path.read_text(encoding="utf-8"))
        jobs = feed_data.get("jobs") or []
        idx.active_feed_count = len(jobs)
        for j in jobs:
            if not isinstance(j, dict):
                continue
            jid = str(j.get("id") or "")
            elig = str(j.get("eligibilityClass") or j.get("eligible") or "")
            if elig == ELIGIBILITY_INDONESIA:
                idx.active_indonesia_count += 1
            elif elig == ELIGIBILITY_GLOBAL:
                idx.active_global_count += 1
            posted = normalize_pilot_posted_date(j.get("officialPostedDate"))
            if is_posted_within_days(posted, run_iso, days=7):
                idx.active_within_7d_count += 1
                if jid:
                    relevant_7d_seen_ids.add(jid)
            idx.register_record(
                job_id=jid,
                company=str(j.get("company") or j.get("platform") or ""),
                title=str(j.get("title") or ""),
                apply_url=str(j.get("applyUrl") or ""),
                official_source_url=str(j.get("officialSourceUrl") or ""),
            )

    unverified_path = state_dir / "unverified_jobs.json"
    if unverified_path.exists():
        unv_data = json.loads(unverified_path.read_text(encoding="utf-8"))
        unv_jobs = unv_data.get("jobs") or []
        idx.unverified_direct_count = len(unv_jobs)
        for j in unv_jobs:
            if not isinstance(j, dict):
                continue
            jid = str(j.get("id") or "")
            posted = normalize_pilot_posted_date(j.get("officialPostedDate"))
            if is_posted_within_days(posted, run_iso, days=7) and jid:
                relevant_7d_seen_ids.add(jid)
            idx.register_record(
                job_id=jid,
                company=str(j.get("company") or j.get("platform") or ""),
                title=str(j.get("title") or ""),
                apply_url=str(j.get("applyUrl") or ""),
                official_source_url=str(j.get("officialSourceUrl") or ""),
            )

    history_path = state_dir / "job_history.json"
    if history_path.exists():
        hist_data = json.loads(history_path.read_text(encoding="utf-8"))
        hist_jobs = hist_data.get("jobs") or {}
        if isinstance(hist_jobs, dict):
            idx.history_tracked_count = len(hist_jobs)
            for jid, rec in hist_jobs.items():
                if not isinstance(rec, dict):
                    continue
                idx.register_record(
                    job_id=str(jid),
                    company=str(rec.get("company") or rec.get("platform") or ""),
                    title=str(rec.get("title") or ""),
                    apply_url=str(rec.get("apply_url") or ""),
                    official_source_url=str(rec.get("official_source_url") or ""),
                )

    cov_path = state_dir / "coverage_report.json"
    if cov_path.exists():
        cov_data = json.loads(cov_path.read_text(encoding="utf-8"))
        summary = cov_data.get("summary") or {}
        idx.raw_retrieved_count = int(summary.get("rawListingsRetrieved") or 0)
        idx.relevant_direct_count = int(summary.get("relevantAiEvalListings") or 0)
        idx.sources_configured = int(summary.get("totalSourcesConfigured") or 14)
        idx.sources_succeeded = int(summary.get("sourcesSucceeded") or 11)
        idx.sources_failed = int(summary.get("sourcesFailed") or 3)
        if idx.unverified_direct_count == 0:
            idx.unverified_direct_count = int(summary.get("unverifiedReviewListings") or 0)

    idx.relevant_within_7d_count = max(idx.active_within_7d_count, len(relevant_7d_seen_ids))
    return idx


def classify_pilot_ai_relevance(clean_raw: RawJobListing) -> Tuple[bool, str, str, List[str], List[str]]:
    core_rel = classify_relevance(clean_raw)
    if core_rel.is_relevant:
        return (
            True,
            "ai_eval_core",
            core_rel.category,
            list(core_rel.matched_signals),
            list(core_rel.extracted_skills),
        )

    if core_rel.rejection_reason == "talent_pool":
        return False, "rejected", "Talent Pool", [], []

    title = (clean_raw.title or "").strip()
    if not title:
        return False, "rejected", "Empty Title", [], []

    if NON_AI_GTM_ADMIN_TITLE_EXCLUSIONS.search(title):
        return False, "rejected", "Non-AI GTM / Administrative Role", [], []

    tags = clean_raw.extra_metadata.get("tags") or []
    tag_str = " ".join(str(t) for t in tags if t)
    dept_str = clean_raw.department or ""

    matched_cat = ""
    matched_signals: List[str] = []
    for cat_label, pat in BROAD_AI_TITLE_RULES:
        m = re.search(pat, title, re.IGNORECASE)
        if m:
            matched_cat = cat_label
            matched_signals.append(f"title:{m.group(0).strip()}")
            break

    if not matched_cat and re.search(r"\b(?:ai|machine\s+learning|artificial\s+intelligence|data\s+science)\b", f"{dept_str} {tag_str}", re.IGNORECASE):
        if re.search(r"\b(?:data\s+scientist|research\s+scientist|research\s+engineer|applied\s+scientist|annotat\w*|linguist\w*)\b", title, re.IGNORECASE):
            m_sub = re.search(r"\b(?:ai|machine\s+learning|llm|nlp|deep\s+learning|generative\s+ai|rlhf)\b", clean_raw.description_text or "", re.IGNORECASE)
            if m_sub:
                matched_cat = "Machine Learning & Data Science"
                matched_signals.append(f"dept_and_body:{m_sub.group(0).strip()}")

    if not matched_cat:
        return False, "rejected", "Irrelevant", [], []

    combined = f"{title}\n{tag_str}\n{(clean_raw.description_text or '')[:1500]}"
    skill_rules = [
        ("Indonesian", r"\b(?:indonesian|bahasa\s+indonesia)\b"),
        ("LLM / GenAI", r"\b(?:llm|large\s+language\s+model|genai|generative\s+ai|rag)\b"),
        ("Machine Learning", r"\b(?:machine\s+learning|deep\s+learning|pytorch|tensorflow)\b"),
        ("NLP / Speech", r"\b(?:nlp|natural\s+language|speech|audio|asr|tts)\b"),
        ("Computer Vision", r"\b(?:computer\s+vision|multimodal|vlm|video|image)\b"),
        ("Prompt Engineering", r"\b(?:prompt\s+engineering|prompt\s+writing|rlhf)\b"),
        ("Python", r"\bpython\b"),
    ]
    skills = [label for label, spat in skill_rules if re.search(spat, combined, re.IGNORECASE)][:6]
    return True, "ai_ml_broad", matched_cat, matched_signals[:4], skills


def classify_pilot_eligibility(clean_raw: RawJobListing) -> Dict[str, Any]:
    loc_restrictions = [
        str(c).strip()
        for c in (clean_raw.extra_metadata.get("location_restrictions") or [])
        if str(c).strip()
    ]
    base_elig = classify_eligibility(clean_raw)
    loc_text = (clean_raw.location_raw or "").strip()

    if base_elig.classification == ELIGIBILITY_RESTRICTED:
        return {
            "status": ELIGIBILITY_RESTRICTED,
            "isVerifiedEligible": False,
            "isIndonesiaEligible": False,
            "confidence": base_elig.confidence,
            "evidence": base_elig.evidence,
            "unresolvedReason": None,
        }

    if loc_restrictions:
        lower_countries = [c.lower() for c in loc_restrictions]
        has_indonesia = any("indonesia" in c or c == "id" or c == "idn" for c in lower_countries)
        if has_indonesia:
            return {
                "status": ELIGIBILITY_INDONESIA,
                "isVerifiedEligible": True,
                "isIndonesiaEligible": True,
                "confidence": "high",
                "evidence": f"Explicit Indonesia in provider locationRestrictions: {loc_restrictions}",
                "unresolvedReason": None,
            }
        has_broad_asia_or_world = any(
            REGIONAL_AMBIGUOUS_LABEL.search(c) or GENERIC_WORLDWIDE_LABEL.search(c)
            for c in lower_countries
        )
        if not has_broad_asia_or_world:
            return {
                "status": ELIGIBILITY_RESTRICTED,
                "isVerifiedEligible": False,
                "isIndonesiaEligible": False,
                "confidence": "high",
                "evidence": f"Restricted to non-Indonesia countries: {', '.join(loc_restrictions[:6])}",
                "unresolvedReason": None,
            }

    loc_parts = [p.strip() for p in re.split(r"[,;/|]+", loc_text) if p.strip()]
    if loc_parts and not re.search(r"\bindonesia\b", loc_text, re.IGNORECASE):
        if all(FOREIGN_ONLY_GEO_LABEL.match(p) for p in loc_parts):
            return {
                "status": ELIGIBILITY_RESTRICTED,
                "isVerifiedEligible": False,
                "isIndonesiaEligible": False,
                "confidence": "high",
                "evidence": f"Restricted to non-Indonesia region(s): '{loc_text}'",
                "unresolvedReason": None,
            }

    if base_elig.classification == ELIGIBILITY_INDONESIA:
        return {
            "status": ELIGIBILITY_INDONESIA,
            "isVerifiedEligible": True,
            "isIndonesiaEligible": True,
            "confidence": base_elig.confidence,
            "evidence": base_elig.evidence,
            "unresolvedReason": None,
        }

    is_generic_ww = bool(
        clean_raw.extra_metadata.get("aggregator_generic_worldwide")
        or base_elig.classification == ELIGIBILITY_GLOBAL
        or GENERIC_WORLDWIDE_LABEL.search(loc_text)
    )
    if is_generic_ww:
        reason = "generic_worldwide_not_proof_of_eligibility"
        evidence = (
            f"Generic global/worldwide aggregator label ('{loc_text}') is not accepted as verified "
            "Indonesia eligibility without explicit residency confirmation"
        )
    elif REGIONAL_AMBIGUOUS_LABEL.search(loc_text):
        reason = "regional_ambiguous_geography"
        evidence = f"Regional label ('{loc_text}') does not confirm Indonesia eligibility"
    else:
        reason = "unspecified_or_unconfirmed_geography"
        evidence = base_elig.evidence

    return {
        "status": ELIGIBILITY_UNVERIFIED,
        "isVerifiedEligible": False,
        "isIndonesiaEligible": False,
        "confidence": "low",
        "evidence": evidence,
        "unresolvedReason": reason,
    }


def resolve_candidate_compensation(clean_raw: RawJobListing) -> Dict[str, Any]:
    structured = clean_raw.extra_metadata.get("structured_compensation")
    if isinstance(structured, dict) and (structured.get("currency") or structured.get("unit")):
        return {
            "min": structured.get("min"),
            "max": structured.get("max"),
            "currency": structured.get("currency"),
            "unit": structured.get("unit"),
            "raw": structured.get("raw"),
        }
    parsed: CompensationInfo = parse_compensation(
        clean_raw.compensation_raw,
        fallback_text=clean_raw.description_text,
    )
    if not parsed.currency and not parsed.unit:
        return {
            "min": parsed.amount_min,
            "max": parsed.amount_max,
            "currency": None,
            "unit": None,
            "raw": parsed.raw_text or None,
        }
    return parsed.to_dict()


class CareerOpsDiscoveryPilot:
    """Runs the isolated Career-Ops-inspired discovery pilot and writes `staging/discovery_candidates.json`."""

    def __init__(
        self,
        pilot_config_path: pathlib.Path,
        staging_feed_path: pathlib.Path,
        state_dir: pathlib.Path,
        output_candidates_path: pathlib.Path,
        http_client: Optional[PoliteHTTPClient] = None,
    ):
        self.pilot_config_path = pilot_config_path
        self.staging_feed_path = staging_feed_path
        self.state_dir = state_dir
        self.output_candidates_path = output_candidates_path
        self.pilot_history_path = state_dir / "discovery_pilot_history.json"
        self.pilot_report_json_path = state_dir / "discovery_pilot_report.json"
        self.pilot_report_md_path = state_dir / "DISCOVERY_PILOT_REPORT.md"
        self.http_client = http_client or PoliteHTTPClient(cache_path=None)

    def _load_pilot_discovery_dates(self) -> Dict[str, str]:
        if not self.pilot_history_path.exists():
            return {}
        try:
            data = json.loads(self.pilot_history_path.read_text(encoding="utf-8"))
            first_seen = data.get("firstDetectedById") or {}
            return {str(k): str(v) for k, v in first_seen.items() if k and v}
        except Exception:
            return {}

    def _save_pilot_discovery_dates(self, first_seen: Dict[str, str], updated_iso: str) -> None:
        write_json_atomically(
            self.pilot_history_path,
            {
                "schemaVersion": "2.1-pilot",
                "lastUpdated": updated_iso,
                "totalTrackedCandidates": len(first_seen),
                "firstDetectedById": dict(sorted(first_seen.items())),
            },
            validate_android_schema=False,
        )

    def run(self, run_timestamp_iso: Optional[str] = None) -> Dict[str, Any]:
        now_iso = run_timestamp_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cfg = json.loads(self.pilot_config_path.read_text(encoding="utf-8"))
        providers_cfg = [p for p in (cfg.get("providers") or []) if p.get("enabled", True)]

        direct_index = load_existing_direct_index(
            staging_feed_path=self.staging_feed_path,
            state_dir=self.state_dir,
            run_iso=now_iso,
        )
        prior_discovery_dates = self._load_pilot_discovery_dates()
        updated_discovery_dates = dict(prior_discovery_dates)

        verified_eligible_candidates: List[Dict[str, Any]] = []
        unverified_candidates: List[Dict[str, Any]] = []
        overlapping_matches: List[Dict[str, Any]] = []
        provider_reports: List[Dict[str, Any]] = []

        seen_cross_provider_urls: Dict[str, str] = {}
        seen_cross_provider_company_title: Dict[Tuple[str, str], str] = {}
        cross_provider_duplicates = 0

        for p_cfg in providers_cfg:
            source_id = str(p_cfg["id"])
            provider_key = str(p_cfg.get("providerKey") or source_id)
            platform_name = str(p_cfg.get("platform") or provider_key.title())
            started = time.monotonic()

            p_report: Dict[str, Any] = {
                "id": source_id,
                "provider": provider_key,
                "platform": platform_name,
                "method": str(p_cfg.get("connector") or ""),
                "paginationType": str(p_cfg.get("paginationType") or ""),
                "status": "SUCCESS",
                "rawRecords": 0,
                "uniqueRelevantAiRecords": 0,
                "aiEvalCoreRecords": 0,
                "aiMlBroadRecords": 0,
                "postedWithin7DaysRecords": 0,
                "missingPostingDateRecords": 0,
                "verifiedIndonesiaEligibleRecords": 0,
                "unverifiedCandidateRecords": 0,
                "restrictedExcludedRecords": 0,
                "overlapWithExistingSources": 0,
                "crossProviderDuplicatesCollapsed": 0,
                "netNewCandidatesEmitted": 0,
                "collectionFailures": 0,
                "durationMs": 0,
                "fetchMetadata": {},
                "termsSummary": str(p_cfg.get("termsSummary") or ""),
                "errorMessage": None,
            }

            try:
                connector = create_pilot_connector(p_cfg, self.http_client)
                raw_listings = connector.fetch_listings()
                p_report["rawRecords"] = len(raw_listings)
                p_report["fetchMetadata"] = getattr(connector, "last_fetch_metadata", {})
            except Exception as exc:
                p_report["status"] = "FAILED"
                p_report["collectionFailures"] = 1
                p_report["errorMessage"] = str(exc)
                p_report["durationMs"] = int((time.monotonic() - started) * 1000)
                logger.error("Discovery pilot provider %s failed: %s", source_id, exc)
                provider_reports.append(p_report)
                continue

            for raw in raw_listings:
                clean_title = strip_html_to_text(raw.title)
                clean_desc = strip_html_to_text(raw.description_text)
                clean_loc = strip_html_to_text(raw.location_raw)
                clean_dept = strip_html_to_text(raw.department)
                clean_emp = strip_html_to_text(raw.employment_type)
                clean_comp = strip_html_to_text(raw.compensation_raw)
                clean_company = strip_html_to_text(raw.platform) or "Unknown Employer"

                clean_raw = RawJobListing(
                    source_connector=raw.source_connector,
                    platform=clean_company,
                    source_job_id=raw.source_job_id,
                    title=clean_title,
                    official_source_url=raw.official_source_url,
                    raw_apply_url=raw.raw_apply_url,
                    location_raw=clean_loc,
                    description_text=clean_desc,
                    compensation_raw=clean_comp,
                    official_posted_at=raw.official_posted_at,
                    employment_type=clean_emp,
                    department=clean_dept,
                    logo_url=raw.logo_url,
                    pure_ai_eval_board=False,
                    is_explicitly_closed=raw.is_explicitly_closed,
                    extra_metadata=raw.extra_metadata,
                )

                is_ai, ai_tier, ai_category, ai_signals, skills = classify_pilot_ai_relevance(clean_raw)
                if not is_ai:
                    continue

                if clean_raw.extra_metadata.get("preserve_exact_apply_url"):
                    clean_apply_url, url_ok, _ = preserve_exact_https_apply_url(clean_raw.raw_apply_url)
                else:
                    clean_apply_url, url_ok, _ = sanitize_official_apply_url(
                        clean_raw.raw_apply_url,
                        fallback_official_url=clean_raw.official_source_url,
                    )
                orig_url, orig_ok, _ = preserve_exact_https_apply_url(clean_raw.official_source_url)
                if not url_ok or not orig_ok:
                    continue

                p_report["uniqueRelevantAiRecords"] += 1
                if ai_tier == "ai_eval_core":
                    p_report["aiEvalCoreRecords"] += 1
                else:
                    p_report["aiMlBroadRecords"] += 1

                posted_iso = normalize_pilot_posted_date(clean_raw.official_posted_at)
                within_7d = is_posted_within_days(posted_iso, now_iso, days=7)
                if posted_iso is None:
                    p_report["missingPostingDateRecords"] += 1
                elif within_7d:
                    p_report["postedWithin7DaysRecords"] += 1

                elig_info = classify_pilot_eligibility(clean_raw)
                elig_status = elig_info["status"]

                canonical_id = build_canonical_id(
                    clean_raw.source_connector,
                    clean_raw.source_job_id,
                    clean_apply_url,
                    clean_title,
                )

                overlaps_direct, matched_direct_id, overlap_reason = direct_index.match(
                    company=clean_company,
                    title=clean_title,
                    apply_url=clean_apply_url,
                    original_source_url=orig_url,
                )
                if overlaps_direct:
                    p_report["overlapWithExistingSources"] += 1
                    overlapping_matches.append(
                        {
                            "candidateId": canonical_id,
                            "provider": provider_key,
                            "company": clean_company,
                            "title": clean_title,
                            "originalSourceUrl": orig_url,
                            "matchedDirectJobId": matched_direct_id,
                            "overlapReason": overlap_reason,
                        }
                    )
                    continue

                if elig_status == ELIGIBILITY_RESTRICTED:
                    p_report["restrictedExcludedRecords"] += 1
                    continue

                if elig_status == ELIGIBILITY_INDONESIA and elig_info["isVerifiedEligible"]:
                    p_report["verifiedIndonesiaEligibleRecords"] += 1
                else:
                    p_report["unverifiedCandidateRecords"] += 1

                cu = canonicalize_dedup_url(clean_apply_url)
                ck_tk = (normalize_company_key(clean_company), normalize_title_key(clean_title))
                if (cu and cu in seen_cross_provider_urls) or (ck_tk[0] and ck_tk[1] and ck_tk in seen_cross_provider_company_title):
                    p_report["crossProviderDuplicatesCollapsed"] += 1
                    cross_provider_duplicates += 1
                    continue
                if cu:
                    seen_cross_provider_urls[cu] = canonical_id
                if ck_tk[0] and ck_tk[1]:
                    seen_cross_provider_company_title[ck_tk] = canonical_id

                discovery_iso = updated_discovery_dates.get(canonical_id, now_iso)
                updated_discovery_dates[canonical_id] = discovery_iso

                comp_dict = resolve_candidate_compensation(clean_raw)
                excerpt = clean_desc[:280].rstrip()
                if len(clean_desc) > 280:
                    excerpt += "..."
                desc_hash = hashlib.sha256(clean_desc.encode("utf-8")).hexdigest()[:16]

                candidate_obj = {
                    "id": canonical_id,
                    "sourceJobId": clean_raw.source_job_id,
                    "provider": provider_key,
                    "sourceConnector": clean_raw.source_connector,
                    "company": clean_company,
                    "title": clean_title,
                    "originalSourceUrl": orig_url,
                    "applyUrl": clean_apply_url,
                    "logo": clean_raw.logo_url or "",
                    "location": clean_loc or "Remote (unspecified)",
                    "postingDate": posted_iso,
                    "postedWithin7Days": within_7d,
                    "discoveryDate": discovery_iso,
                    "lastCheckedAt": now_iso,
                    "compensation": comp_dict,
                    "aiRelevance": {
                        "isAiRelated": True,
                        "tier": ai_tier,
                        "category": ai_category,
                        "matchedSignals": ai_signals,
                        "skills": skills,
                    },
                    "eligibility": {
                        "classification": elig_status,
                        "isVerifiedEligible": elig_info["isVerifiedEligible"],
                        "isIndonesiaEligible": elig_info["isIndonesiaEligible"],
                        "confidence": elig_info["confidence"],
                        "evidence": elig_info["evidence"],
                        "unresolvedReason": elig_info["unresolvedReason"],
                    },
                    "excerpt": excerpt,
                    "fullDescriptionHash": desc_hash,
                    "attribution": clean_raw.extra_metadata.get("source_attribution") or {
                        "provider": platform_name,
                        "listingUrl": orig_url,
                        "requiresLinkBack": True,
                    },
                }

                if elig_info["isVerifiedEligible"]:
                    verified_eligible_candidates.append(candidate_obj)
                else:
                    unverified_candidates.append(candidate_obj)
                p_report["netNewCandidatesEmitted"] += 1

            p_report["durationMs"] = int((time.monotonic() - started) * 1000)
            provider_reports.append(p_report)

        def _chronological_sort_key(item: Dict[str, Any]) -> Tuple[int, str, str]:
            p_date = item.get("postingDate")
            if p_date:
                return (0, _invert_iso_string(p_date), item["id"])
            return (1, _invert_iso_string(item.get("discoveryDate") or ""), item["id"])

        verified_eligible_candidates.sort(key=_chronological_sort_key)
        unverified_candidates.sort(key=_chronological_sort_key)

        summary = {
            "providersConfigured": len(providers_cfg),
            "providersSucceeded": sum(1 for r in provider_reports if r["status"] == "SUCCESS"),
            "providersFailed": sum(1 for r in provider_reports if r["status"] != "SUCCESS"),
            "collectionFailures": sum(r["collectionFailures"] for r in provider_reports),
            "rawRecordsRetrieved": sum(r["rawRecords"] for r in provider_reports),
            "uniqueRelevantAiRecords": sum(r["uniqueRelevantAiRecords"] for r in provider_reports),
            "aiEvalCoreRecords": sum(r["aiEvalCoreRecords"] for r in provider_reports),
            "aiMlBroadRecords": sum(r["aiMlBroadRecords"] for r in provider_reports),
            "postedWithin7DaysRecords": sum(r["postedWithin7DaysRecords"] for r in provider_reports),
            "missingPostingDateRecords": sum(r["missingPostingDateRecords"] for r in provider_reports),
            "verifiedIndonesiaEligibleRecords": len(verified_eligible_candidates),
            "unresolvedEligibilityRecords": len(unverified_candidates),
            "restrictedExcludedRecords": sum(r["restrictedExcludedRecords"] for r in provider_reports),
            "overlapWithExistingDirectSources": len(overlapping_matches),
            "crossProviderDuplicatesCollapsed": cross_provider_duplicates,
            "netNewVerifiedCandidates": len(verified_eligible_candidates),
            "netNewUnverifiedCandidates": len(unverified_candidates),
        }

        baseline_comparison = {
            "sourceGroup": "Existing v2 Direct Backend (14 configured: 11 working, 3 failed)",
            "sourcesConfigured": direct_index.sources_configured,
            "sourcesSucceeded": direct_index.sources_succeeded,
            "collectionFailures": direct_index.sources_failed,
            "rawRecords": direct_index.raw_retrieved_count,
            "uniqueRelevantRecords": direct_index.relevant_direct_count,
            "recentWithin7DaysRecords": direct_index.relevant_within_7d_count,
            "activeFeedWithin7DaysRecords": direct_index.active_within_7d_count,
            "verifiedEligibleRecords": direct_index.active_feed_count,
            "verifiedIndonesiaEligibleRecords": direct_index.active_indonesia_count,
            "verifiedRemoteGlobalRecords": direct_index.active_global_count,
            "unverifiedRecords": direct_index.unverified_direct_count,
            "historicalTrackedIds": direct_index.history_tracked_count,
        }

        feed_payload = {
            "schemaVersion": "2.1-experimental",
            "pipeline": "ai-jobs-hub-v2.1-career-ops-pilot",
            "experimentalOnly": True,
            "doNotPromoteToAndroidFeed": True,
            "generatedAt": now_iso,
            "upstreamAttribution": cfg.get("upstreamInspiration") or {},
            "existingBackendBaseline": baseline_comparison,
            "summary": summary,
            "providerReports": provider_reports,
            "verifiedEligibleCandidates": verified_eligible_candidates,
            "unverifiedCandidates": unverified_candidates,
            "overlappingWithExistingSources": overlapping_matches,
        }

        write_json_atomically(self.output_candidates_path, feed_payload, validate_android_schema=False)
        self._save_pilot_discovery_dates(updated_discovery_dates, updated_iso=now_iso)

        report_payload = {
            "generatedAt": now_iso,
            "existingBackendBaseline": baseline_comparison,
            "summary": summary,
            "providerReports": provider_reports,
            "sampleVerifiedTitles": [
                {"id": c["id"], "company": c["company"], "title": c["title"], "location": c["location"], "postingDate": c["postingDate"]}
                for c in verified_eligible_candidates[:10]
            ],
            "sampleUnverifiedAiEvalCoreTitles": [
                {"id": c["id"], "provider": c["provider"], "company": c["company"], "title": c["title"], "location": c["location"], "postingDate": c["postingDate"]}
                for c in unverified_candidates if c["aiRelevance"]["tier"] == "ai_eval_core"
            ][:15],
        }
        write_json_atomically(self.pilot_report_json_path, report_payload, validate_android_schema=False)
        self.pilot_report_md_path.parent.mkdir(parents=True, exist_ok=True)
        self.pilot_report_md_path.write_text(
            render_pilot_markdown_report(now_iso, baseline_comparison, summary, provider_reports),
            encoding="utf-8",
        )
        return feed_payload


def _invert_iso_string(iso_str: str) -> str:
    return "".join(chr(255 - ord(ch)) for ch in iso_str[:24])


def render_pilot_markdown_report(
    generated_at: str,
    baseline: Dict[str, Any],
    summary: Dict[str, Any],
    provider_reports: List[Dict[str, Any]],
) -> str:
    lines = [
        "# AI Jobs Hub v2.1 — Career-Ops Inspired Discovery Pilot Report",
        "",
        f"- **Generated At (UTC):** `{generated_at}`",
        "- **Mode:** Isolated Experimental Discovery (`staging/discovery_candidates.json` — Android `staging/jobs_feed.json` untouched)",
        "- **Upstream Reference:** `santifer/career-ops` (`career-ops-hq/career-ops`, MIT License, Copyright (c) 2026 Santiago Fernández de Valderrama)",
        "",
        "## Source & Provider Comparison",
        "",
        "| Source / Provider | Status | Raw Records | Unique Relevant AI Records (Core Eval / Broad AI) | Recent (<= 7d Posted) | Verified Eligible | Unverified / Unresolved | Overlap w/ Direct | Restricted Excluded | Collection Failures |",
        "| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| **Existing Direct Backend (11/14 active)** | `BASELINE` | {baseline['rawRecords']} | "
            f"{baseline['uniqueRelevantRecords']} ({baseline['uniqueRelevantRecords']} / 0) | "
            f"{baseline['recentWithin7DaysRecords']} (feed: {baseline['activeFeedWithin7DaysRecords']}) | "
            f"{baseline['verifiedEligibleRecords']} ({baseline['verifiedIndonesiaEligibleRecords']} ID + {baseline['verifiedRemoteGlobalRecords']} Global) | "
            f"{baseline['unverifiedRecords']} | - | - | {baseline['collectionFailures']} |"
        ),
    ]
    for r in provider_reports:
        lines.append(
            f"| **{r['platform']}** (`{r['id']}`) | `{r['status']}` | {r['rawRecords']} | "
            f"{r['uniqueRelevantAiRecords']} ({r['aiEvalCoreRecords']} / {r['aiMlBroadRecords']}) | "
            f"{r['postedWithin7DaysRecords']} | {r['verifiedIndonesiaEligibleRecords']} | "
            f"{r['unverifiedCandidateRecords']} | {r['overlapWithExistingSources']} | "
            f"{r['restrictedExcludedRecords']} | {r['collectionFailures']} |"
        )
    lines.append(
        f"| **Pilot Total (Deduplicated Net-New)** | `{summary['providersSucceeded']}/{summary['providersConfigured']} OK` | "
        f"{summary['rawRecordsRetrieved']} | {summary['uniqueRelevantAiRecords']} ({summary['aiEvalCoreRecords']} / {summary['aiMlBroadRecords']}) | "
        f"{summary['postedWithin7DaysRecords']} | {summary['verifiedIndonesiaEligibleRecords']} | "
        f"{summary['unresolvedEligibilityRecords']} | {summary['overlapWithExistingDirectSources']} (+{summary['crossProviderDuplicatesCollapsed']} cross-board) | "
        f"{summary['restrictedExcludedRecords']} | {summary['collectionFailures']} |"
    )
    lines.extend(
        [
            "",
            "## Provider Diagnostics & Licensing Notes",
            "",
        ]
    )
    for r in provider_reports:
        meta_str = json.dumps(r.get("fetchMetadata") or {}, sort_keys=True)
        err_suffix = f" | Error: `{r['errorMessage']}`" if r.get("errorMessage") else ""
        lines.append(
            f"- **{r['platform']} (`{r['method']}`, `{r['paginationType']}`, `{r['durationMs']} ms`)**: "
            f"metadata=`{meta_str}`{err_suffix} — {r['termsSummary']}"
        )
    lines.append("")
    return "\n".join(lines)


def validate_pilot_candidates_payload(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    required_keys = {
        "schemaVersion",
        "pipeline",
        "experimentalOnly",
        "doNotPromoteToAndroidFeed",
        "generatedAt",
        "summary",
        "providerReports",
        "verifiedEligibleCandidates",
        "unverifiedCandidates",
    }
    missing = required_keys - set(payload.keys())
    if missing:
        errors.append(f"Missing top-level keys: {sorted(missing)}")
        return False, errors

    if payload.get("experimentalOnly") is not True or payload.get("doNotPromoteToAndroidFeed") is not True:
        errors.append("Experimental safety flags must remain True")

    seen_ids: Set[str] = set()
    for bucket_name, expected_verified in (
        ("verifiedEligibleCandidates", True),
        ("unverifiedCandidates", False),
    ):
        rows = payload.get(bucket_name)
        if not isinstance(rows, list):
            errors.append(f"{bucket_name} must be a list")
            continue
        for idx, item in enumerate(rows):
            cid = str(item.get("id") or "")
            if not cid:
                errors.append(f"{bucket_name}[{idx}] missing id")
            elif cid in seen_ids:
                errors.append(f"Duplicate candidate id across pilot buckets: {cid}")
            seen_ids.add(cid)

            elig = item.get("eligibility") or {}
            if bool(elig.get("isVerifiedEligible")) != expected_verified:
                errors.append(f"{bucket_name}[{idx}] ({cid}) has mismatched isVerifiedEligible={elig.get('isVerifiedEligible')}")
            if expected_verified and elig.get("classification") != ELIGIBILITY_INDONESIA:
                errors.append(f"verifiedEligibleCandidates[{idx}] ({cid}) must be '{ELIGIBILITY_INDONESIA}', got {elig.get('classification')!r}")
            if not expected_verified and elig.get("classification") != ELIGIBILITY_UNVERIFIED:
                errors.append(f"unverifiedCandidates[{idx}] ({cid}) must be '{ELIGIBILITY_UNVERIFIED}', got {elig.get('classification')!r}")

            for url_key in ("originalSourceUrl", "applyUrl"):
                u = str(item.get(url_key) or "")
                if not u.startswith("https://"):
                    errors.append(f"{bucket_name}[{idx}] ({cid}) invalid {url_key}: {u!r}")

            if item.get("postingDate") is None and item.get("postedWithin7Days") is True:
                errors.append(f"{bucket_name}[{idx}] ({cid}) cannot mark postedWithin7Days=True when postingDate is null")

    return len(errors) == 0, errors
