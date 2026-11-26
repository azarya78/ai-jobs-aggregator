from datetime import datetime, timezone
from mindrift_audit import audit

def test_recent_publication_old_creation_and_similarity():
    now = datetime(2026,11,26,tzinfo=timezone.utc)
    rows = [{"shortcode":c,"published_on":"2026-11-25T00:00:00Z","created_at":"2025-01-01T00:00:00Z","description":"Same task"} for c in ("a","b")]
    catalog = [{"id":"web_mindrift:"+c,"sourceConnector":"web_mindrift","sourceJobId":c,"title":"AI Trainer","location":c,"officialPostedDate":"2026-11-25T00:00:00Z"} for c in ("a","b")]
    x = audit(rows,catalog,now)
    assert x["counts"]["recent_created_older"] == 2
    assert x["recent_title_description_groups"] == 1
    assert x["multi_shortcode_groups"] == 1

def test_unmatched_not_invented():
    x = audit([], [{"id":"web_mindrift:a","sourceConnector":"web_mindrift"}],datetime(2026,11,26,tzinfo=timezone.utc))
    assert x["counts"]["unmatched_catalog"] == 1
