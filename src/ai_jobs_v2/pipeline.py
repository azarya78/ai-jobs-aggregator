"""Multi-Source Pipeline Orchestrator with Independent Connector Fault Isolation & Drop Safeguard."""
from __future__ import annotations
import json
import logging
import pathlib
import re
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set

from .connectors import create_connector
from .http_client import PoliteHTTPClient, redact_secrets
from .models import (
    ConnectorReport,
    NormalizedJobRecord,
    ELIGIBILITY_INDONESIA,
    ELIGIBILITY_GLOBAL,
    ELIGIBILITY_RESTRICTED,
    ELIGIBILITY_UNVERIFIED,
    STATUS_CLOSED,
)
from .normalizer import normalize_raw_listing, build_canonical_id
from .url_sanitizer import sanitize_official_apply_url
from .history import JobHistoryStore
from .validator import write_json_atomically
from .freshness_metrics import build_source_freshness
from .discovery_history import DiscoveryHistory
from .project_groups import build_project_groups
from .reporter import (
    build_coverage_report_dict,
    render_coverage_markdown,
    extract_daily_new_digest_candidates,
)

logger = logging.getLogger("ai_jobs_v2.pipeline")


class JobDiscoveryPipeline:
    ANOMALOUS_SOURCE_WIPEOUT_MIN_ACTIVE = 3

    def __init__(
        self,
        sources_config_path: pathlib.Path,
        state_dir: pathlib.Path,
        output_feed_path: pathlib.Path,
        http_client: Optional[PoliteHTTPClient] = None,
        boolean_eligible: bool = False,
    ):
        self.sources_config_path = sources_config_path
        self.state_dir = state_dir
        self.output_feed_path = output_feed_path
        self.boolean_eligible = boolean_eligible
        self.http_client = http_client or PoliteHTTPClient(cache_path=state_dir / "http_cache.json")
        self.history_store = JobHistoryStore(state_dir / "job_history.json")

    def run(self, run_timestamp_iso: Optional[str] = None) -> Dict[str, Any]:
        now_iso = run_timestamp_iso or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cfg = json.loads(self.sources_config_path.read_text(encoding="utf-8"))
        sources = [s for s in cfg.get("sources", []) if s.get("enabled", True)]

        prev_last_ok: Dict[str, Optional[str]] = {}
        cov_path = self.state_dir / "coverage_report.json"
        if cov_path.exists():
            try:
                old_cov = json.loads(cov_path.read_text(encoding="utf-8"))
                for s in old_cov.get("sources", []):
                    prev_last_ok[s["source_id"]] = s.get("last_successful_check")
            except Exception:
                pass

        # Count previously active published records per source to guard against silent source-wide wipeouts
        prev_active_by_source: Dict[str, Set[str]] = {}
        for entry in self.history_store.records.values():
            if (entry.get("job_status") != STATUS_CLOSED and not entry.get("withheld_reason")
                    and entry.get("eligibility_class") in (ELIGIBILITY_INDONESIA, ELIGIBILITY_GLOBAL)):
                src_key = str(entry.get("source_connector") or "")
                if src_key:
                    prev_active_by_source.setdefault(src_key, set()).add(entry["canonical_id"])

        connector_reports: List[ConnectorReport] = []
        primary_eligible_candidates: List[NormalizedJobRecord] = []
        unverified_review_records: List[NormalizedJobRecord] = []
        # Independent catalog for broad search. Never used as history input.
        discovery_catalog_records: List[NormalizedJobRecord] = []
        withheld: Dict[str, str] = {}
        explicitly_closed_ids: Set[str] = set()
        succeeded_connectors: Set[str] = set()
        failed_connectors: Set[str] = set()

        for src_cfg in sources:
            sid = src_cfg["id"]
            t0 = time.monotonic()
            report = ConnectorReport(
                source_id=sid,
                platform=src_cfg["platform"],
                official_url=src_cfg["official_url"],
                method=src_cfg["method"],
                status="SUCCESS",
                last_successful_check=prev_last_ok.get(sid),
            )
            try:
                connector = create_connector(src_cfg, self.http_client)
                raw_listings = connector.fetch_listings()
                report.raw_count = len(raw_listings)
                if not raw_listings and src_cfg.get("method", "").endswith("_public_web"):
                    raise RuntimeError(
                        f"Public HTML extractor returned zero listings for {sid}; "
                        "possible page-layout or rendering change"
                    )

                source_eligible: List[NormalizedJobRecord] = []
                source_unverified: List[NormalizedJobRecord] = []
                source_catalog: List[NormalizedJobRecord] = []
                source_closed_ids: Set[str] = set()
                source_withheld: Dict[str, str] = {}

                for raw in raw_listings:
                    if raw.is_explicitly_closed:
                        report.closed_in_payload_count += 1
                        clean_u, _, _ = sanitize_official_apply_url(raw.raw_apply_url, raw.official_source_url)
                        cid = build_canonical_id(raw.source_connector, raw.source_job_id, clean_u, raw.title)
                        source_closed_ids.add(cid)
                        continue

                    norm_rec, rel_res, elig_res = normalize_raw_listing(raw, collected_at_iso=now_iso)
                    if norm_rec is None or not rel_res.is_relevant or elig_res is None:
                        clean_u, _, _ = sanitize_official_apply_url(raw.raw_apply_url, raw.official_source_url)
                        cid = build_canonical_id(raw.source_connector, raw.source_job_id, clean_u, raw.title)
                        source_withheld[cid] = rel_res.rejection_reason or "out_of_scope"
                        report.rejected_irrelevant_count += 1
                        continue

                    report.relevant_count += 1
                    # Preserve location-restricted and uncertain roles for transparent
                    # exploration; no claims of application eligibility are implied.
                    if norm_rec.apply_url_available:
                        source_catalog.append(norm_rec)
                    if elig_res.classification == ELIGIBILITY_INDONESIA:
                        report.indonesia_eligible_count += 1
                        source_eligible.append(norm_rec)
                    elif elig_res.classification == ELIGIBILITY_GLOBAL:
                        report.remote_global_count += 1
                        source_eligible.append(norm_rec)
                    elif elig_res.classification == ELIGIBILITY_UNVERIFIED:
                        report.unverified_review_count += 1
                        source_unverified.append(norm_rec)
                    else:
                        source_withheld[norm_rec.canonical_id] = ELIGIBILITY_RESTRICTED
                        report.restricted_count += 1

                # Safeguard: prevent unexplained 100% source-wide drops on multi-job sources from silently closing active records
                prior_active = prev_active_by_source.get(sid, set())
                if (
                    len(prior_active) >= self.ANOMALOUS_SOURCE_WIPEOUT_MIN_ACTIVE
                    and len(source_eligible) == 0
                    and prior_active.difference(source_withheld, source_closed_ids, (rec.canonical_id for rec in source_unverified))
                ):
                    raise RuntimeError(
                        f"Anomalous source-wide drop for {sid}: {len(prior_active)} previously active jobs dropped to 0 "
                        f"(raw={report.raw_count}) without explicit closure markers; holding existing jobs UNVERIFIED"
                    )

                report.last_successful_check = now_iso
                succeeded_connectors.add(sid)
                report.status = "EMPTY" if len(raw_listings) == 0 else "SUCCESS"
                primary_eligible_candidates.extend(source_eligible)
                unverified_review_records.extend(source_unverified)
                discovery_catalog_records.extend(source_catalog)
                explicitly_closed_ids.update(source_closed_ids)
                withheld.update(source_withheld)

            except Exception as exc:
                err_clean = redact_secrets(str(exc))
                logger.error("Connector %s failed independently: %s", sid, err_clean)
                report.status = "FAILED"
                report.error_message = err_clean[:240]
                failed_connectors.add(sid)
            finally:
                report.duration_ms = int((time.monotonic() - t0) * 1000)
                connector_reports.append(report)

        reconciled_active, dedup_summary = self.history_store.reconcile(
            incoming_records=primary_eligible_candidates,
            explicitly_closed_ids=explicitly_closed_ids,
            succeeded_connectors=succeeded_connectors,
            failed_connectors=failed_connectors,
            run_timestamp_iso=now_iso,
            unverified_records=unverified_review_records,
            withheld_records=withheld,
        )

        def _sort_key(r: NormalizedJobRecord):
            tier = 0 if r.eligibility_class == ELIGIBILITY_INDONESIA else 1
            date_iso = r.official_posted_date or r.first_detected_at
            return (tier, -int(datetime.fromisoformat(date_iso.replace("Z", "+00:00")).timestamp()))

        reconciled_active.sort(key=_sort_key)

        feed_items = [r.to_android_feed_item(boolean_eligible=self.boolean_eligible) for r in reconciled_active]
        feed_payload = {
            "lastUpdated": now_iso,
            "totalJobs": len(feed_items),
            "schemaVersion": "2.0",
            "pipeline": "ai-jobs-hub-v2-direct",
            "jobs": feed_items,
        }

        # Experimental all-location catalog, independent of published eligible feed.
        # Official posting dates take precedence; unknown dates sort LAST rather
        # than being presented as newly posted based on first detection.
        by_id: Dict[str, NormalizedJobRecord] = {}
        for rec in discovery_catalog_records:
            if rec.canonical_id not in by_id:
                by_id[rec.canonical_id] = rec
        catalog = list(by_id.values())
        catalog.sort(key=lambda rec: (
            rec.official_posted_date is None,
            -(int(datetime.fromisoformat(rec.official_posted_date.replace("Z", "+00:00")).timestamp())
              if rec.official_posted_date else 0),
            rec.canonical_id,
        ))
        # Preserve first discovery across successive runs. A repeated scan must
        # never make an old posting appear newly discovered in Android.
        previous_catalog_by_id: Dict[str, Dict[str, Any]] = {}
        catalog_path = self.output_feed_path.parent / "freshness_catalog.json"
        has_previous_catalog = False
        if catalog_path.exists():
            try:
                old_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
                if isinstance(old_catalog.get("jobs"), list):
                    has_previous_catalog = True
                    previous_catalog_by_id = {
                        j["id"]: j for j in old_catalog["jobs"]
                        if isinstance(j, dict) and isinstance(j.get("id"), str)
                    }
            except (OSError, ValueError, TypeError):
                logger.warning("Cannot reuse previous freshness catalog discovery dates")

        # Source IDs identify distinct upstream records, not necessarily distinct
        # opportunities. Workable and other boards can publish a single task many
        # times with different shortcodes. Collapse only identical visible jobs
        # (platform + normalized title + location + pay + description excerpt).
        # Keep different regions, rates, or substantial descriptions separate.
        def _semantic_key(rec: NormalizedJobRecord):
            def norm(value: str) -> str:
                return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", value.casefold())).strip()
            return (
                norm(rec.company), norm(rec.title), norm(rec.location),
                norm(rec.compensation.raw_text), norm(rec.short_description),
            )

        kept_by_semantics: Dict[tuple, NormalizedJobRecord] = {}
        duplicate_counts: Dict[str, int] = {}
        for rec in catalog:
            key = _semantic_key(rec)
            if key in kept_by_semantics:
                representative = kept_by_semantics[key]
                duplicate_counts[representative.canonical_id] = duplicate_counts.get(representative.canonical_id, 0) + 1
                continue
            kept_by_semantics[key] = rec
        collapsed_duplicates = len(catalog) - len(kept_by_semantics)
        catalog = list(kept_by_semantics.values())

        discovery_history = DiscoveryHistory(self.output_feed_path.parent / "discovery_history.json")
        catalog_items = []
        for rec in catalog:
            item = rec.to_android_feed_item(boolean_eligible=False)
            previous = previous_catalog_by_id.get(rec.canonical_id)
            stored = self.history_store.records.get(rec.canonical_id) or {}
            old_first_seen = (
                (previous or {}).get("firstDetectedAt")
                or stored.get("first_detected_at")
            )
            if isinstance(old_first_seen, str) and old_first_seen:
                item["firstDetectedAt"] = old_first_seen
                item["firstSeenAt"] = old_first_seen
            if previous:
                item["jobStatus"] = (
                    "EXISTING" if previous.get("fullDescriptionHash") == item["fullDescriptionHash"]
                    else "UPDATED"
                )
            elif stored:
                item["jobStatus"] = stored.get("job_status", item["jobStatus"])
            item["eligibilityDisclaimer"] = (
                "Location eligibility is informational; check the official application requirements."
            )
            item["dateSource"] = "official_posted" if rec.official_posted_date else "first_detected"
            if duplicate_counts.get(rec.canonical_id):
                item["duplicateListingsCollapsed"] = duplicate_counts[rec.canonical_id]
            discovery_history.observe(item, now_iso, previous, stored)
            catalog_items.append(item)
        project_groups = build_project_groups(catalog_items, now_iso)
        write_json_atomically(
            self.output_feed_path.parent / "freshness_catalog.json",
            {
                "schemaVersion": "2.1-experimental",
                "pipeline": "ai-jobs-hub-v2.1-direct-freshness-catalog",
                "experimentalOnly": True,
                "doNotPromoteToAndroidFeed": True,
                "lastUpdated": now_iso,
                "totalJobs": len(catalog_items),
                "sourceRecordsBeforeSemanticDedup": len(discovery_catalog_records),
                "semanticDuplicatesCollapsed": collapsed_duplicates,
                "jobs": catalog_items,
                "projectGrouping": project_groups,
            },
            validate_android_schema=False,
        )

        discovery_history.save(now_iso)
        write_json_atomically(self.output_feed_path, feed_payload, validate_android_schema=True)
        self.history_store.save_atomic(updated_at_iso=now_iso)
        self.http_client.save_cache()

        dedup_unverified = {}
        for urec in unverified_review_records:
            dedup_unverified.setdefault(urec.canonical_id, urec.to_android_feed_item(boolean_eligible=self.boolean_eligible))
        write_json_atomically(
            self.state_dir / "unverified_jobs.json",
            {
                "lastUpdated": now_iso,
                "totalUnverified": len(dedup_unverified),
                "jobs": list(dedup_unverified.values()),
            },
            validate_android_schema=False,
        )

        write_json_atomically(
            self.state_dir / "daily_new_candidates.json",
            extract_daily_new_digest_candidates(reconciled_active, now_iso),
            validate_android_schema=False,
        )

        cov_dict = build_coverage_report_dict(
            run_timestamp_iso=now_iso,
            connector_reports=connector_reports,
            dedup_summary=dedup_summary,
            published_count=len(feed_items),
            unverified_queue_count=len(dedup_unverified),
        )
        cov_dict["sourceFreshness"] = build_source_freshness(
            catalog_items, connector_reports, previous_catalog_by_id, now_iso, has_previous_catalog
        )
        write_json_atomically(self.state_dir / "coverage_report.json", cov_dict, validate_android_schema=False)
        md_text = render_coverage_markdown(cov_dict)
        md_tmp = (self.state_dir / "COVERAGE_REPORT.md").with_suffix(".tmp")
        md_tmp.write_text(md_text, encoding="utf-8")
        md_tmp.replace(self.state_dir / "COVERAGE_REPORT.md")

        return cov_dict
