"""Deterministic checks for read-only source radar safety and link extraction."""
import unittest
from audit_new_sources import count_opportunity_links, inspect_source

class Response:
    def __init__(self, status, text="", url="https://app.begun.com/opportunities", content_type="text/html"):
        self.status_code = status
        self.text = text
        self.url = url
        self.headers = {"Content-Type": content_type}

class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
    def get(self, *args, **kwargs):
        return next(self.responses)

class TestOfficialSourceRadar(unittest.TestCase):
    SOURCE = {"id":"radar_begun","platform":"Begun","url":"https://app.begun.com/opportunities",
              "host":"app.begun.com","pathPrefix":"/opportunities/"}

    def test_only_exact_official_host_and_matching_path(self):
        html = """<a href="/opportunities/ai-specialist?referral=x">One</a>
        <a href="https://app.begun.com/opportunities/ai-specialist">Duplicate</a>
        <a href="/opportunities/language-rater/">Two</a>
        <a href="https://evil.example/opportunities/fake">No</a>
        <a href="/contact">No</a>"""
        urls = count_opportunity_links(html,"https://app.begun.com/opportunities","app.begun.com","/opportunities/")
        self.assertEqual(urls,["https://app.begun.com/opportunities/ai-specialist",
                               "https://app.begun.com/opportunities/language-rater"])

    def test_robots_disallow_fails_closed_without_fetching_page(self):
        session=Session([Response(200,"User-agent: *\nDisallow: /opportunities")])
        result=inspect_source(session,self.SOURCE)
        self.assertEqual(result["status"],"ROBOTS_NOT_CLEARED")
        self.assertEqual(result["matchingLinkCount"],0)

    def test_static_links_are_discovery_evidence_not_publication_permission(self):
        session=Session([Response(404),Response(200,'<a href="/opportunities/safety-rater">Rater</a>')])
        result=inspect_source(session,self.SOURCE)
        self.assertEqual(result["status"],"PUBLIC_LIST_LINKS")
        self.assertEqual(result["matchingLinkCount"],1)
        self.assertFalse(result["syndicationApproved"])
        self.assertFalse(result["postingDatesVerified"])

    def test_redirect_to_external_host_is_not_accepted(self):
        session=Session([Response(404),Response(200,"",url="https://other.example/jobs")])
        result=inspect_source(session,self.SOURCE)
        self.assertEqual(result["status"],"REDIRECT_OUTSIDE_CONFIGURED_HOST")

if __name__=="__main__":
    unittest.main()
