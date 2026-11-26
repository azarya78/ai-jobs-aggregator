from types import SimpleNamespace
from ai_jobs_v2.freshness_metrics import build_source_freshness

NOW = "2026-11-26T12:00:00Z"
def source(method="ashby_api", status="SUCCESS"):
    return SimpleNamespace(source_id="a", platform="A", method=method, status=status)
def item(date, detected=NOW):
    return dict(id="1", sourceConnector="a", officialPostedDate=date, firstDetectedAt=detected)
def metrics(items, report=None, prev=None, baseline=True):
    return build_source_freshness(items, [report or source()], prev or {}, NOW, baseline)["sources"][0]
def test_old_job_newly_detected_not_fresh():
    x = metrics([item("2025-01-01T00:00:00Z")])
    assert x["reported_7d"] == 0 and x["first_detected_24h"] == 1
def test_ashby_republication_not_first_publication():
    x = metrics([item(NOW)], prev={"1":item("2025-01-01T00:00:00Z")})
    assert x["reported_24h"] == 1 and x["possible_republication_7d"] == 1
    assert x["first_publication_7d"] is None
def test_greenhouse_first_publication():
    x = metrics([item(NOW)], source("greenhouse_api"))
    assert x["first_publication_24h"] == x["first_publication_7d"] == 1
def test_failed_is_unknown():
    assert metrics([], source(status="FAILED"))["reported_7d"] is None
def test_future_and_invalid_dates():
    for date in (None, "invalid", "2026-11-27T12:00:00Z"):
        x = metrics([item(date)])
        assert x["undated_or_invalid"] == 1 and x["reported_7d"] == 0
def test_missing_baseline_is_unknown():
    x = metrics([item(NOW)], baseline=False)
    assert x["possible_republication_7d"] is None and x["absent_previous_snapshot"] is None
