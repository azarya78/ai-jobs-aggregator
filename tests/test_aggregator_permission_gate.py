"""Aggregator access must not imply permission to publish."""
import copy
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from validate_aggregator_permissions import validate_gate

def fixture():
    return json.loads((Path(__file__).resolve().parents[1]/"config/aggregator_permission_gate.json").read_text())

def test_current_sources_are_research_only():
    summary=validate_gate(fixture())
    assert summary=={"sources":3,"publicationApproved":0,"researchOnly":3}

def test_public_rss_is_not_redistribution_permission():
    j=fixture()
    j["sources"][0]["decision"]="PUBLISH_APPROVED"
    with pytest.raises(ValueError,match="both permissions"):
        validate_gate(j)

def test_missing_evidence_disallows_publishing_even_if_flags_granted():
    j=fixture()
    x=j["sources"][1]
    x["automatedCollectionPermission"]="GRANTED"
    x["republishingPermission"]="GRANTED"
    x["decision"]="PUBLISH_APPROVED"
    with pytest.raises(ValueError,match="documented permission"):
        validate_gate(j)

def test_research_cannot_auto_fetch():
    j=fixture()
    j["sources"][2]["automaticFetchEnabled"]=True
    with pytest.raises(ValueError,match="Research-only"):
        validate_gate(j)

def test_permission_documented_and_reviewed_can_advance():
    j=fixture()
    x=j["sources"][2]
    x.update({"automatedCollectionPermission":"GRANTED",
              "republishingPermission":"GRANTED",
              "decision":"PUBLISH_APPROVED",
              "permissionEvidenceUrl":"https://annotation.academy/example-authorized-license",
              "permissionReviewedAt":"2026-11-26"})
    assert validate_gate(j)["publicationApproved"]==1
