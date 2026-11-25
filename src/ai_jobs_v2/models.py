"""Core data structures for AI Jobs Hub v2."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any


ELIGIBILITY_INDONESIA = "Indonesia Eligible"
ELIGIBILITY_GLOBAL = "Remote Global"
ELIGIBILITY_RESTRICTED = "Restricted"
ELIGIBILITY_UNVERIFIED = "Unverified"

STATUS_NEW = "NEW"
STATUS_EXISTING = "EXISTING"
STATUS_UPDATED = "UPDATED"
STATUS_CLOSED = "CLOSED"
STATUS_UNVERIFIED = "UNVERIFIED"


@dataclass
class CompensationInfo:
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    currency: Optional[str] = None
    unit: Optional[str] = None  # "hourly" | "per_task" | "per_project" | "monthly" | "annual" | None
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "min": self.amount_min,
            "max": self.amount_max,
            "currency": self.currency,
            "unit": self.unit,
            "raw": self.raw_text or None,
        }


@dataclass
class RelevanceResult:
    is_relevant: bool
    category: str
    matched_signals: List[str] = field(default_factory=list)
    rejection_reason: Optional[str] = None
    extracted_skills: List[str] = field(default_factory=list)


@dataclass
class EligibilityResult:
    classification: str  # Indonesia Eligible | Remote Global | Restricted | Unverified
    confidence: str      # high | medium | low
    evidence: str
    is_indonesian_language: bool = False


@dataclass
class RawJobListing:
    source_connector: str
    platform: str
    source_job_id: str
    title: str
    official_source_url: str
    raw_apply_url: str
    location_raw: str = ""
    description_text: str = ""
    compensation_raw: str = ""
    official_posted_at: Optional[str] = None
    employment_type: str = ""
    department: str = ""
    logo_url: str = ""
    pure_ai_eval_board: bool = False
    is_explicitly_closed: bool = False
    extra_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedJobRecord:
    canonical_id: str
    source_job_id: str
    title: str
    company: str
    logo: str
    official_source_url: str
    apply_url: str
    apply_url_available: bool
    category: str
    skills: List[str]
    location: str
    eligibility_class: str
    eligibility_confidence: str
    eligibility_evidence: str
    is_indonesian: bool
    is_direct: bool
    compensation: CompensationInfo
    short_description: str
    full_description_hash: str
    official_posted_date: Optional[str]
    first_detected_at: str
    last_verified_at: str
    job_status: str
    source_connector: str
    collected_at: str

    requirements_evidence: List[str] = field(default_factory=list)

    def to_android_feed_item(self, boolean_eligible: bool = False) -> Dict[str, Any]:
        """Serialize to backward-compatible Android jobs_feed.json item schema + optional v2 fields."""
        from datetime import datetime, timezone

        date_str = (self.official_posted_date or self.first_detected_at)[:10]
        iso_for_ts = self.official_posted_date or self.first_detected_at
        try:
            dt = datetime.fromisoformat(iso_for_ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            ts_ms = int(dt.timestamp() * 1000)
        except Exception:
            dt_fallback = datetime.fromisoformat(self.first_detected_at.replace("Z", "+00:00"))
            ts_ms = int(dt_fallback.timestamp() * 1000)

        eligible_val: Any = (
            self.eligibility_class in (ELIGIBILITY_INDONESIA, ELIGIBILITY_GLOBAL)
            if boolean_eligible
            else self.eligibility_class
        )

        return {
            # --- Required Legacy Android v1 Fields ---
            "id": self.canonical_id,
            "title": self.title,
            "company": self.company,
            "logo": self.logo,
            "category": self.category,
            "eligible": eligible_val,
            "isIndonesian": self.is_indonesian,
            "isDirect": self.is_direct,
            "skills": self.skills,
            "location": self.location or "Remote",
            "pay": self.compensation.raw_text,
            "date": date_str,
            "timestamp": ts_ms,
            "excerpt": self.short_description,
            "applyUrl": self.apply_url,
            "source": self.source_connector,
            # --- Additive v2 Fields (Optional / Non-Breaking) ---
            "sourceJobId": self.source_job_id,
            "officialSourceUrl": self.official_source_url,
            "applyUrlAvailable": self.apply_url_available,
            "eligibilityClass": self.eligibility_class,
            "eligibilityConfidence": self.eligibility_confidence,
            "eligibilityEvidence": self.eligibility_evidence,
            "requirementsEvidence": self.requirements_evidence,
            "compensation": self.compensation.to_dict(),
            "fullDescriptionHash": self.full_description_hash,
            "officialPostedDate": self.official_posted_date,
            "firstDetectedAt": self.first_detected_at,
            "firstSeenAt": self.first_detected_at,
            "lastVerifiedAt": self.last_verified_at,
            "jobStatus": self.job_status,
            "sourceConnector": self.source_connector,
            "collectedAt": self.collected_at,
        }


@dataclass
class ConnectorReport:
    source_id: str
    platform: str
    official_url: str
    method: str
    status: str  # "SUCCESS" | "EMPTY" | "NOT_MODIFIED" | "FAILED"
    raw_count: int = 0
    relevant_count: int = 0
    indonesia_eligible_count: int = 0
    remote_global_count: int = 0
    unverified_review_count: int = 0
    restricted_count: int = 0
    rejected_irrelevant_count: int = 0
    closed_in_payload_count: int = 0
    last_successful_check: Optional[str] = None
    duration_ms: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
