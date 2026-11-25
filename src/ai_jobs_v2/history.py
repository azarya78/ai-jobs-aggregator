"""Persistent Job History, Cross-Source Deduplication, and Fault-Safe Lifecycle Engine."""
from __future__ import annotations
import json
import pathlib
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Any, Optional

from .models import (
    NormalizedJobRecord,
    CompensationInfo,
    STATUS_NEW,
    STATUS_EXISTING,
    STATUS_UPDATED,
    STATUS_CLOSED,
    STATUS_UNVERIFIED,
)


@dataclass
class DeduplicationSummary:
    total_input_records: int = 0
    duplicates_collapsed: int = 0
    new_count: int = 0
    existing_count: int = 0
    updated_count: int = 0
    closed_count: int = 0
    unverified_connector_hold_count: int = 0


class JobHistoryStore:
    def __init__(self, history_path: pathlib.Path):
        self.history_path = history_path
        self.records: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> None:
        if not self.history_path.exists():
            self.records = {}
            return
        raw = json.loads(self.history_path.read_text(encoding="utf-8"))
        self.records = raw.get("jobs", {}) if isinstance(raw, dict) else {}

    def save_atomic(self, updated_at_iso: str) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schemaVersion": "2.0",
            "lastUpdated": updated_at_iso,
            "totalTrackedJobs": len(self.records),
            "jobs": dict(sorted(self.records.items())),
        }
        tmp = self.history_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.history_path)

    def reconcile(
        self,
        incoming_records: List[NormalizedJobRecord],
        explicitly_closed_ids: Set[str],
        succeeded_connectors: Set[str],
        failed_connectors: Set[str],
        run_timestamp_iso: str,
    ) -> Tuple[List[NormalizedJobRecord], DeduplicationSummary]:
        summary = DeduplicationSummary(total_input_records=len(incoming_records))

        url_to_existing_cid: Dict[str, str] = {}
        for cid, entry in self.records.items():
            u = entry.get("apply_url") or ""
            if u:
                url_to_existing_cid[u] = cid

        seen_canonical_ids: Set[str] = set()
        seen_apply_urls: Dict[str, NormalizedJobRecord] = {}
        deduped_active: List[NormalizedJobRecord] = []

        for rec in incoming_records:
            if rec.canonical_id not in self.records and rec.apply_url and rec.apply_url in url_to_existing_cid:
                rec.canonical_id = url_to_existing_cid[rec.apply_url]

            if rec.canonical_id in seen_canonical_ids:
                summary.duplicates_collapsed += 1
                continue

            if rec.apply_url and rec.apply_url in seen_apply_urls:
                summary.duplicates_collapsed += 1
                continue

            seen_canonical_ids.add(rec.canonical_id)
            if rec.apply_url:
                seen_apply_urls[rec.apply_url] = rec

            prev = self.records.get(rec.canonical_id)
            if prev is None:
                rec.job_status = STATUS_NEW
                rec.first_detected_at = run_timestamp_iso
                rec.last_verified_at = run_timestamp_iso
                summary.new_count += 1
            else:
                rec.first_detected_at = prev.get("first_detected_at") or run_timestamp_iso
                rec.last_verified_at = run_timestamp_iso
                prev_hash = prev.get("full_description_hash")
                prev_elig = prev.get("eligibility_class")
                prev_apply = prev.get("apply_url")
                prev_status = prev.get("job_status")

                if (
                    prev_status == STATUS_CLOSED
                    or prev_hash != rec.full_description_hash
                    or prev_elig != rec.eligibility_class
                    or prev_apply != rec.apply_url
                ):
                    rec.job_status = STATUS_UPDATED
                    summary.updated_count += 1
                else:
                    rec.job_status = STATUS_EXISTING
                    summary.existing_count += 1

            self.records[rec.canonical_id] = self._serialize_history_entry(rec)
            deduped_active.append(rec)

        for closed_cid in explicitly_closed_ids:
            if closed_cid in self.records and self.records[closed_cid].get("job_status") != STATUS_CLOSED:
                self.records[closed_cid]["job_status"] = STATUS_CLOSED
                self.records[closed_cid]["closed_at"] = run_timestamp_iso
                summary.closed_count += 1

        for cid, entry in list(self.records.items()):
            if cid in seen_canonical_ids or cid in explicitly_closed_ids:
                continue
            src = entry.get("source_connector", "")
            prev_status = entry.get("job_status", STATUS_EXISTING)

            if src in failed_connectors:
                if prev_status != STATUS_CLOSED:
                    entry["job_status"] = STATUS_UNVERIFIED
                    entry["last_unverified_at"] = run_timestamp_iso
                    summary.unverified_connector_hold_count += 1
                    restored = self._deserialize_history_entry(entry)
                    if restored is not None:
                        deduped_active.append(restored)
            elif src in succeeded_connectors:
                if prev_status != STATUS_CLOSED:
                    entry["job_status"] = STATUS_CLOSED
                    entry["closed_at"] = run_timestamp_iso
                    summary.closed_count += 1

        return deduped_active, summary

    @staticmethod
    def _serialize_history_entry(rec: NormalizedJobRecord) -> Dict[str, Any]:
        return {
            "canonical_id": rec.canonical_id,
            "source_job_id": rec.source_job_id,
            "title": rec.title,
            "company": rec.company,
            "logo": rec.logo,
            "official_source_url": rec.official_source_url,
            "apply_url": rec.apply_url,
            "apply_url_available": rec.apply_url_available,
            "category": rec.category,
            "skills": rec.skills,
            "location": rec.location,
            "eligibility_class": rec.eligibility_class,
            "eligibility_confidence": rec.eligibility_confidence,
            "eligibility_evidence": rec.eligibility_evidence,
            "is_indonesian": rec.is_indonesian,
            "is_direct": rec.is_direct,
            "compensation": rec.compensation.to_dict(),
            "short_description": rec.short_description,
            "full_description_hash": rec.full_description_hash,
            "official_posted_date": rec.official_posted_date,
            "first_detected_at": rec.first_detected_at,
            "last_verified_at": rec.last_verified_at,
            "job_status": rec.job_status,
            "source_connector": rec.source_connector,
            "collected_at": rec.collected_at,
        }

    @staticmethod
    def _deserialize_history_entry(entry: Dict[str, Any]) -> Optional[NormalizedJobRecord]:
        try:
            comp_d = entry.get("compensation") or {}
            comp = CompensationInfo(
                amount_min=comp_d.get("min"),
                amount_max=comp_d.get("max"),
                currency=comp_d.get("currency"),
                unit=comp_d.get("unit"),
                raw_text=comp_d.get("raw") or "",
            )
            return NormalizedJobRecord(
                canonical_id=entry["canonical_id"],
                source_job_id=entry.get("source_job_id", ""),
                title=entry["title"],
                company=entry["company"],
                logo=entry.get("logo", ""),
                official_source_url=entry.get("official_source_url", ""),
                apply_url=entry.get("apply_url", ""),
                apply_url_available=bool(entry.get("apply_url_available", False)),
                category=entry.get("category", "AI Training"),
                skills=list(entry.get("skills") or []),
                location=entry.get("location", "Remote"),
                eligibility_class=entry.get("eligibility_class", "Unverified"),
                eligibility_confidence=entry.get("eligibility_confidence", "low"),
                eligibility_evidence=entry.get("eligibility_evidence", ""),
                is_indonesian=bool(entry.get("is_indonesian", False)),
                is_direct=bool(entry.get("is_direct", True)),
                compensation=comp,
                short_description=entry.get("short_description", ""),
                full_description_hash=entry.get("full_description_hash", ""),
                official_posted_date=entry.get("official_posted_date"),
                first_detected_at=entry["first_detected_at"],
                last_verified_at=entry["last_verified_at"],
                job_status=entry.get("job_status", STATUS_UNVERIFIED),
                source_connector=entry.get("source_connector", ""),
                collected_at=entry.get("collected_at", entry["last_verified_at"]),
            )
        except Exception:
            return None
