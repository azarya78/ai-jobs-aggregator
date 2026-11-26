"""Read-only research safety and link-normalization tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_third_party_sources import analyze_html, inspect, permitted

def test_manual_only_never_makes_request():
    class Never:
        def get(self, *args, **kw):
            raise AssertionError("network must not be requested")
    row = inspect(Never(), {"id":"r", "name":"RemoWork","url":"https://remowork.life/jobs",
                             "mode":"manual_only","reason":"terms restrict scraping"})
    assert row["state"] == "MANUAL_REVIEW_REQUIRED"
    assert row["reusePermissionVerified"] is False

def test_internal_paths_external_domains_no_tracking_urls():
    html = """<a href="/jobs/one?ref=123">a</a>
    <a href="/jobs/one?ref=456">duplicate</a>
    <a href="https://jobs.micro1.ai/post/abc?token=SECRET">Micro1</a>
    <a href="https://jobs.micro1.ai/post/other?utm_campaign=x">Micro1</a>
    <a href="https://www.google.com/search?q=secret">google</a>
    <a href="http://evil.example/private">not https</a>
    <a href="javascript:alert(1)">invalid</a>"""
    n, hosts = analyze_html(html,"https://annotation.academy/jobs",
                            "annotation.academy","/jobs/")
    assert n == 1
    assert hosts == {"jobs.micro1.ai": 2}
    assert "SECRET" not in str(hosts)

def test_robots_failure_fails_closed():
    class Response:
        status_code = 403
    class Fake:
        def get(self, *args, **kw):
            return Response()
    allowed, reason = permitted(Fake(),"https://annotation.academy/jobs")
    assert not allowed
    assert reason == "robots_status_403"
