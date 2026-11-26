from aitrainer_candidate import url_key, candidate
def test_url_preserves_job_query():
    assert url_key("https://example.com/jobs?id=1&utm_source=a") == url_key("https://example.com/jobs?id=1")
    assert url_key("https://example.com/jobs?id=1") != url_key("https://example.com/jobs?id=2")
def test_invalid_row():
    assert candidate({"title":"AI Trainer"}, "2026-11-26T00:00:00Z") is None
def test_date_never_inferred():
    rec=candidate({"id":"1","platform":"micro1","title":"AI Trainer","description":"Evaluate AI model responses using rubrics","apply_url":"https://example.com/jobs/1","first_seen":"2026-11-26"}, "2026-11-26T00:00:00Z")
    assert rec is not None
    assert rec.official_posted_date is None
    assert rec.is_direct is False
