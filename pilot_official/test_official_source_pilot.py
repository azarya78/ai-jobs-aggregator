from datetime import datetime, timezone
from official_source_pilot import posting, age_hours, key

def test_update_not_publication():
    assert posting({"updated_at":"2026-11-26T00:00:00Z"}, "greenhouse") is None

def test_first_publication_wins():
    assert posting({"first_published":"2025-01-01", "updated_at":"2026-11-26"}, "greenhouse") == "2025-01-01"

def test_age_and_future():
    now = datetime(2026,11,26,tzinfo=timezone.utc)
    assert age_hours("2026-11-25T00:00:00Z", now) == 24
    assert age_hours("2026-11-27", now) is None
    assert age_hours(None, now) is None

def test_url_key():
    assert key("https://example.com/job/1?utm_source=x") == key("https://example.com/job/1/")
    assert key("https://example.com/") is None
