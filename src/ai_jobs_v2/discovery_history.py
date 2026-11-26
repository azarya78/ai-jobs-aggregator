"""Persistent discovery observations; never infer publication from discovery."""
import json
from .freshness_metrics import parse_date
from .validator import write_json_atomically

class DiscoveryHistory:
    def __init__(self, path):
        self.path = path
        self.records = {}
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schemaVersion") != 1 or not isinstance(payload.get("jobs"), dict):
                raise ValueError("Invalid discovery history; refusing to reset")
            self.records = payload["jobs"]

    def observe(self, item, now, previous=None, stored=None):
        cid = item["id"]
        entry = self.records.get(cid, {})
        previous = previous or {}
        stored = stored or {}
        candidates = [entry.get("firstDetectedAt"), previous.get("firstDetectedAt"),
                      stored.get("first_detected_at"), item.get("firstDetectedAt"), now]
        now_dt = parse_date(now)
        valid = [v for v in candidates if parse_date(v) and parse_date(v) <= now_dt]
        first = min(valid, key=parse_date)
        old_date = entry.get("lastReportedPostingDate") or previous.get("officialPostedDate")
        new_date = item.get("officialPostedDate")
        old_dt, new_dt = parse_date(old_date), parse_date(new_date)
        advanced = bool(old_dt and new_dt and old_dt < new_dt <= now_dt)
        item["firstDetectedAt"] = item["firstSeenAt"] = first
        item["previouslyObserved"] = bool(entry or previous or stored)
        item["reportedDateAdvanced"] = advanced
        if entry and not previous:
            item["reappearedAfterSnapshotGap"] = True
            item["jobStatus"] = "EXISTING" if entry.get("fullDescriptionHash") == item.get("fullDescriptionHash") else "UPDATED"
        self.records[cid] = {
            "firstDetectedAt": first,
            "lastObservedAt": now,
            "lastReportedPostingDate": new_date if new_dt and new_dt <= now_dt else old_date,
            "fullDescriptionHash": item.get("fullDescriptionHash"),
        }

    def save(self, now):
        write_json_atomically(self.path, {"schemaVersion": 1, "lastUpdated": now,
            "totalTrackedJobs": len(self.records), "jobs": self.records}, validate_android_schema=False)
