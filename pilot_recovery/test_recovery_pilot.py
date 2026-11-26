from recovery_pilot import extract, inspect
from unittest.mock import Mock

def test_matching_links_only():
    result=extract('<a href="/jobs/123-ai-rater">job</a><a href="/jobs/123-ai-rater?utm=x">dup</a><a href="https://other.test/jobs/123-ai-rater">foreign</a>',"https://example.test/search",r"^/jobs/\d+-[^/]+/?$")
    assert result["matchingLinks"]==1

def test_robots_denial_stops_request():
    session=Mock()
    session.get.return_value.status_code=403
    result=inspect(session,"test","https://example.test/jobs",r"/jobs/.*")
    assert result["status"]=="ROBOTS_NOT_CLEARED"
    assert session.get.call_count==1


def test_cross_origin_robots_redirect_stops():
    session=Mock()
    session.get.return_value.status_code=308
    session.get.return_value.headers={"Location":"https://other.test/robots.txt"}
    result=inspect(session,"test","https://example.test/jobs",r"/jobs/.*")
    assert result["status"]=="ROBOTS_NOT_CLEARED"
    assert session.get.call_count==1
