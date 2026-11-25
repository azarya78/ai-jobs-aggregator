"""Atomic feed writer and strict Android jobs_feed.json backward-compatibility validator."""
from __future__ import annotations
import json
import pathlib
from typing import Dict, Any, List, Tuple
from urllib.parse import urlparse

from .url_sanitizer import BLOCKED_REFERRAL_DOMAINS

REQUIRED_ROOT_KEYS = {"lastUpdated", "totalJobs", "jobs"}

REQUIRED_JOB_FIELD_TYPES = {
    "id": str,
    "title": str,
    "company": str,
    "logo": str,
    "category": str,
    "isIndonesian": bool,
    "isDirect": bool,
    "skills": list,
    "location": str,
    "pay": str,
    "date": str,
    "timestamp": int,
    "excerpt": str,
    "applyUrl": str,
    "source": str,
}


def validate_android_feed_payload(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    errors: List[str] = []
    if not isinstance(payload, dict):
        return False, ["Root JSON must be an object"]

    missing_root = REQUIRED_ROOT_KEYS - set(payload.keys())
    if missing_root:
        errors.append(f"Missing required root keys: {sorted(missing_root)}")
        return False, errors

    if not isinstance(payload["lastUpdated"], str) or not payload["lastUpdated"].endswith("Z"):
        errors.append("Root 'lastUpdated' must be an ISO-8601 UTC string ending in 'Z'")

    jobs = payload["jobs"]
    if not isinstance(jobs, list):
        errors.append("Root 'jobs' must be a JSON array")
        return False, errors

    if payload["totalJobs"] != len(jobs):
        errors.append(f"Root 'totalJobs' ({payload['totalJobs']}) != len(jobs) ({len(jobs)})")

    seen_ids = set()
    for idx, item in enumerate(jobs):
        if not isinstance(item, dict):
            errors.append(f"jobs[{idx}] is not an object")
            continue
        for field_name, expected_type in REQUIRED_JOB_FIELD_TYPES.items():
            if field_name not in item:
                errors.append(f"jobs[{idx}] missing required field '{field_name}'")
            elif not isinstance(item[field_name], expected_type):
                errors.append(
                    f"jobs[{idx}].{field_name} has type {type(item[field_name]).__name__}, expected {expected_type.__name__}"
                )

        if "eligible" not in item or not isinstance(item["eligible"], (str, bool)):
            errors.append(f"jobs[{idx}].eligible must be str or bool")

        jid = item.get("id")
        if jid in seen_ids:
            errors.append(f"Duplicate job id in feed: {jid}")
        seen_ids.add(jid)

        if not (item.get("title") or "").strip():
            errors.append(f"jobs[{idx}] has empty title")

        apply_url = item.get("applyUrl") or ""
        if apply_url:
            parsed = urlparse(apply_url)
            if parsed.scheme != "https":
                errors.append(f"jobs[{idx}].applyUrl must use https: {apply_url}")
            host = (parsed.hostname or "").lower()
            if host in BLOCKED_REFERRAL_DOMAINS:
                errors.append(f"jobs[{idx}].applyUrl uses blocked referral domain: {host}")

    return len(errors) == 0, errors


def write_json_atomically(target_path: pathlib.Path, payload: Dict[str, Any], validate_android_schema: bool = False) -> None:
    if validate_android_schema:
        ok, errs = validate_android_feed_payload(payload)
        if not ok:
            raise ValueError(f"Android feed schema validation failed: {'; '.join(errs[:10])}")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    json.loads(serialized)

    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    tmp_path.write_text(serialized, encoding="utf-8")
    tmp_path.replace(target_path)
