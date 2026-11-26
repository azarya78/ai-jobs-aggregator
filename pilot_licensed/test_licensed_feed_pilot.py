import pytest
from licensed_feed_pilot import inspect, rows_from

def test_nested_feed():
    result = inspect({"data":{"jobs":[{"platform":"A","title":"Test"},{"platform":"A","title":""}]},"total":10})
    assert result["container"] == "data.jobs"
    assert result["platform_counts"] == {"A":2}
    assert result["populated_fields"]["title"] == 1
    assert result["pagination_metadata"] == {"total":10}

def test_unknown_schema():
    with pytest.raises(ValueError):
        rows_from({"error":"not a feed"})
