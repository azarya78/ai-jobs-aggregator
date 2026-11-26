"""Read-only source-level freshness metrics for the deduplicated catalog."""
from datetime import datetime, timezone

DATE_BASIS = {
    "greenhouse_api": "first_publication",
    "ashby_api": "latest_publication",
    "lever_api": "posting_creation",
    "smartrecruiters_api": "release_date",
    "teamtailor_feed": "reported_publication",
}

def parse_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, OverflowError):
        return None

def hours_since(value, now):
    dt = parse_date(value)
    if dt is None or dt > now:
        return None
    return (now - dt).total_seconds() / 3600

def build_source_freshness(items, reports, previous_by_id, now_iso, has_baseline):
    now = parse_date(now_iso)
    if now is None:
        raise ValueError("Invalid collection timestamp")
    result = []
    for report in reports:
        basis = DATE_BASIS.get(report.method, "mixed_or_unverified")
        row = dict(source_id=report.source_id, platform=report.platform, status=report.status,
                   date_basis=basis)
        fields = ("catalog_jobs", "dated_jobs", "undated_or_invalid", "reported_24h",
                  "reported_7d", "first_publication_24h", "first_publication_7d",
                  "first_detected_24h", "first_detected_7d", "date_advanced_existing",
                  "possible_republication_7d", "absent_previous_snapshot")
        if report.status not in ("SUCCESS", "EMPTY", "NOT_MODIFIED"):
            row.update(dict.fromkeys(fields))
            result.append(row)
            continue
        row.update(dict.fromkeys(fields, 0))
        if not has_baseline:
            for field in ("date_advanced_existing", "possible_republication_7d", "absent_previous_snapshot"):
                row[field] = None
        if basis != "first_publication":
            row["first_publication_24h"] = row["first_publication_7d"] = None
        for item in items:
            if item.get("sourceConnector") != report.source_id:
                continue
            row["catalog_jobs"] += 1
            age = hours_since(item.get("officialPostedDate"), now)
            if age is None:
                row["undated_or_invalid"] += 1
            else:
                row["dated_jobs"] += 1
                for suffix, limit in (("24h", 24), ("7d", 168)):
                    if age <= limit:
                        row["reported_" + suffix] += 1
                        if basis == "first_publication":
                            row["first_publication_" + suffix] += 1
            detected = hours_since(item.get("firstDetectedAt"), now)
            if detected is not None:
                row["first_detected_24h"] += int(detected <= 24)
                row["first_detected_7d"] += int(detected <= 168)
            if has_baseline:
                prev = previous_by_id.get(item["id"])
                if prev is None:
                    row["absent_previous_snapshot"] += 1
                else:
                    old = parse_date(prev.get("officialPostedDate"))
                    new = parse_date(item.get("officialPostedDate"))
                    if old and new and old < new <= now:
                        row["date_advanced_existing"] += 1
                        if basis == "latest_publication" and age is not None and age <= 168:
                            row["possible_republication_7d"] += 1
        result.append(row)
    return {"generatedAt": now_iso, "previousSnapshotAvailable": has_baseline,
            "scope": "current catalog after existing within-platform semantic deduplication",
            "limitations": ["Reported dates are not necessarily first publications.",
                "First detection is not posting freshness and may reset after disappearance.",
                "Possible republication compares the same canonical ID in consecutive snapshots only.",
                "Failed sources have null metrics; cross-platform uniqueness is not established."],
            "sources": result}
