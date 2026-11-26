"""Fail-closed policy gate for third-party aggregator feed publication.

This script does not fetch external websites. It reads policy metadata and
prevents research sources from being treated as approved Android feed inputs.
"""
import json
from pathlib import Path

ALLOWED_DECISIONS = {"RESEARCH_ONLY", "PILOT_ALLOWED", "PUBLISH_APPROVED"}
ALLOWED_PERMISSION = {"NOT_GRANTED", "UNVERIFIED", "GRANTED"}

def validate_gate(document):
    if document.get("schemaVersion") != "aggregator-permission-gate-v1":
        raise ValueError("Unexpected permission schema")
    if document.get("experimentalOnly") is not True or document.get("doNotPublishToAndroid") is not True:
        raise ValueError("Research registry must remain isolated")
    sources = document.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Missing sources")
    ids = set()
    for source in sources:
        source_id = source.get("id")
        if not isinstance(source_id, str) or source_id in ids:
            raise ValueError("Duplicate or invalid source ID")
        ids.add(source_id)
        decision = source.get("decision")
        if decision not in ALLOWED_DECISIONS:
            raise ValueError("Unknown decision")
        collect = source.get("automatedCollectionPermission")
        reuse = source.get("republishingPermission")
        if collect not in ALLOWED_PERMISSION or reuse not in ALLOWED_PERMISSION:
            raise ValueError("Missing permission classification")
        if decision == "PUBLISH_APPROVED":
            if collect != "GRANTED" or reuse != "GRANTED":
                raise ValueError("Publication prohibited without both permissions")
            if not source.get("permissionEvidenceUrl") or not source.get("permissionReviewedAt"):
                raise ValueError("Publication requires documented permission evidence and review date")
        elif decision == "PILOT_ALLOWED":
            if collect != "GRANTED" or not source.get("permissionEvidenceUrl"):
                raise ValueError("Pilot requires documented collection permission")
        if decision == "RESEARCH_ONLY" and source.get("automaticFetchEnabled", False):
            raise ValueError("Research-only source cannot run an automated collector")
    return {"sources":len(sources), "publicationApproved":sum(x["decision"]=="PUBLISH_APPROVED" for x in sources), "researchOnly":sum(x["decision"]=="RESEARCH_ONLY" for x in sources)}

def main():
    cfg=json.loads(Path("config/aggregator_permission_gate.json").read_text(encoding="utf-8"))
    report=validate_gate(cfg)
    print("Aggregator permission gate:",json.dumps(report,sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
