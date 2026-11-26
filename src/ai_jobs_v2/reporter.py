"""Source Coverage Report & Daily Digest Candidate Exporter."""
from __future__ import annotations
from typing import List, Dict, Any
from .models import ConnectorReport, NormalizedJobRecord, STATUS_NEW
from .history import DeduplicationSummary


def build_coverage_report_dict(
    run_timestamp_iso: str,
    connector_reports: List[ConnectorReport],
    dedup_summary: DeduplicationSummary,
    published_count: int,
    unverified_queue_count: int,
) -> Dict[str, Any]:
    return {
        "generatedAt": run_timestamp_iso,
        "summary": {
            "totalSourcesConfigured": len(connector_reports),
            "sourcesSucceeded": sum(1 for r in connector_reports if r.status in ("SUCCESS", "EMPTY", "NOT_MODIFIED")),
            "sourcesEmptyZeroResults": sum(1 for r in connector_reports if r.status == "EMPTY"),
            "sourcesFailed": sum(1 for r in connector_reports if r.status == "FAILED"),
            "rawListingsRetrieved": sum(r.raw_count for r in connector_reports),
            "relevantAiEvalListings": sum(r.relevant_count for r in connector_reports),
            "indonesiaEligibleListings": sum(r.indonesia_eligible_count for r in connector_reports),
            "remoteGlobalListings": sum(r.remote_global_count for r in connector_reports),
            "unverifiedReviewListings": unverified_queue_count,
            "restrictedListingsFiltered": sum(r.restricted_count for r in connector_reports),
            "irrelevantRejectedListings": sum(r.rejected_irrelevant_count for r in connector_reports),
            "publishedAndroidFeedTotal": published_count,
            "deduplication": {
                "duplicatesCollapsed": dedup_summary.duplicates_collapsed,
                "newJobs": dedup_summary.new_count,
                "existingUnchanged": dedup_summary.existing_count,
                "updatedJobs": dedup_summary.updated_count,
                "closedJobs": dedup_summary.closed_count,
                "unverifiedHeldDueToConnectorFailure": dedup_summary.unverified_connector_hold_count,
            },
        },
        "sources": [r.to_dict() for r in connector_reports],
    }


def render_coverage_markdown(report_dict: Dict[str, Any]) -> str:
    s = report_dict["summary"]
    d = s["deduplication"]
    lines = [
        "# AI Jobs Hub v2 — Source Coverage Report",
        "",
        f"- **Generated At (UTC):** `{report_dict['generatedAt']}`",
        f"- **Published to Android Feed (`Indonesia Eligible` + `Remote Global`):** **{s['publishedAndroidFeedTotal']}**",
        f"- **Held in Eligibility Review Queue (`Unverified`):** **{s['unverifiedReviewListings']}**",
        f"- **Lifecycle Delta:** `{d['newJobs']} NEW` | `{d['updatedJobs']} UPDATED` | `{d['existingUnchanged']} EXISTING` | `{d['closedJobs']} CLOSED` | `{d['duplicatesCollapsed']} DUPLICATES COLLAPSED`",
        "",
        "| Platform | Official URL | Method | Status | Raw | Relevant | ID Eligible | Global | Review | Rejected | Last Check | Errors / Notes |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for src in report_dict["sources"]:
        err = (src.get("error_message") or "—").replace("|", "\\|")
        last_chk = src.get("last_successful_check") or "—"
        lines.append(
            f"| {src['platform']} | {src['official_url']} | `{src['method']}` | **{src['status']}** | "
            f"{src['raw_count']} | {src['relevant_count']} | {src['indonesia_eligible_count']} | "
            f"{src['remote_global_count']} | {src['unverified_review_count']} | {src['rejected_irrelevant_count']} | "
            f"`{last_chk}` | {err} |"
        )
    lines.append("")
    if "sourceFreshness" in report_dict:
        lines.extend(["## Source freshness (deduplicated catalog)", "",
            "Reported dates are not always first publications. First detection is not posting freshness.",
            "",
            "| Platform | Date basis | Catalog | Reported 24h | Reported 7d | First publication 24h | First publication 7d | Undated/invalid | First detected 24h | Possible republication 7d |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"])
        for row in report_dict["sourceFreshness"]["sources"]:
            fields = ("catalog_jobs", "reported_24h", "reported_7d", "first_publication_24h",
                      "first_publication_7d", "undated_or_invalid", "first_detected_24h", "possible_republication_7d")
            values = ["—" if row[k] is None else str(row[k]) for k in fields]
            lines.append("| " + " | ".join([row["platform"], row["date_basis"], *values]) + " |")
        lines.append("")
    return "\n".join(lines)


def extract_daily_new_digest_candidates(active_feed_records: List[NormalizedJobRecord], run_timestamp_iso: str) -> Dict[str, Any]:
    new_items = [r.to_android_feed_item() for r in active_feed_records if r.job_status == STATUS_NEW]
    return {
        "generatedAt": run_timestamp_iso,
        "count": len(new_items),
        "newOpportunities": new_items,
    }
