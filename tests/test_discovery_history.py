from ai_jobs_v2.discovery_history import DiscoveryHistory

DAY1 = "2026-11-01T00:00:00Z"
DAY2 = "2026-11-02T00:00:00Z"
DAY3 = "2026-11-20T00:00:00Z"

def job(now, posting=DAY1):
    return dict(id="test:1", firstDetectedAt=now, officialPostedDate=posting,
                fullDescriptionHash="abc", jobStatus="NEW")

def test_reappearance_preserves_first_seen(tmp_path):
    path = tmp_path / "history.json"
    h = DiscoveryHistory(path)
    h.observe(job(DAY1), DAY1)
    h.save(DAY1)
    h = DiscoveryHistory(path)
    h.save(DAY2)
    h = DiscoveryHistory(path)
    item = job(DAY3)
    h.observe(item, DAY3)
    assert item["firstSeenAt"] == DAY1
    assert item["reappearedAfterSnapshotGap"] is True
    assert item["jobStatus"] == "EXISTING"

def test_migrate_previous_snapshot(tmp_path):
    h = DiscoveryHistory(tmp_path / "history.json")
    item = job(DAY3)
    h.observe(item, DAY3, previous=job(DAY1))
    assert item["firstDetectedAt"] == DAY1

def test_date_advance_not_new_discovery(tmp_path):
    h = DiscoveryHistory(tmp_path / "history.json")
    h.observe(job(DAY1), DAY1)
    item = job(DAY3, DAY3)
    h.observe(item, DAY3)
    assert item["reportedDateAdvanced"] and item["firstDetectedAt"] == DAY1

def test_corrupt_history_fails_closed(tmp_path):
    import pytest
    path = tmp_path / "history.json"
    path.write_text("{}")
    with pytest.raises(ValueError):
        DiscoveryHistory(path)
