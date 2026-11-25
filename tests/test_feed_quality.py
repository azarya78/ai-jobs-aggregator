import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from ai_jobs_v2.compensation import parse_compensation
from ai_jobs_v2.eligibility import classify_eligibility
from ai_jobs_v2.models import RawJobListing
from ai_jobs_v2.pipeline import JobDiscoveryPipeline
from ai_jobs_v2.relevance import classify_relevance
from ai_jobs_v2.url_sanitizer import sanitize_official_apply_url
from ai_jobs_v2.validator import validate_android_feed_payload


def listing(title="AI Trainer", location="Indonesia", description="", **kwargs):
    return RawJobListing("lever_test", "Test", "123", title, "https://example.com/jobs/123",
                         "https://example.com/jobs/123/apply", location_raw=location,
                         description_text=description, **kwargs)


class TestFeedQuality(unittest.TestCase):
    def test_language_and_company_footprint_do_not_set_geography(self):
        job = listing("Evaluator - Bahasa Indonesia", "Remote", "Our company operates across 180 countries.")
        self.assertEqual(classify_eligibility(job).classification, "Unverified")
        job.description_text = "Our network operates in countries: Germany, Indonesia, Japan."
        self.assertEqual(classify_eligibility(job).classification, "Unverified")

    def test_legal_restriction_precedes_worldwide_and_indonesia(self):
        job = listing(location="Worldwide", description="Open to applicants worldwide. US work authorization required.")
        self.assertEqual(classify_eligibility(job).classification, "Restricted")
        job.location_raw = "Indonesia"
        self.assertEqual(classify_eligibility(job).classification, "Restricted")

    def test_non_ai_translation_and_explicit_future_opportunity(self):
        job = listing("English to Indonesian linguist for Patent", description=(
            "As a trusted global transformation partner, we annotate AI data. OVERVIEW "
            "Translate and proofread patent documents using CAT tools."))
        self.assertFalse(classify_relevance(job).is_relevant)
        job.description_text += " Evaluate machine translated text."
        self.assertTrue(classify_relevance(job).is_relevant)
        pool = listing(description="This is not an active job opening. Join our network.")
        self.assertEqual(classify_relevance(pool).rejection_reason, "talent_pool")
        support = listing("IT Support Specialist", description="Our company offers data annotation.")
        self.assertFalse(classify_relevance(support).is_relevant)

    def test_sparse_mercor_card_does_not_invent_work_or_hourly_pay(self):
        from ai_jobs_v2.connectors.public_web import MercorConnector
        conn = MercorConnector(dict(id="web_mercor", platform="Mercor", method="mercor_public_web",
                                    official_url="https://work.mercor.com", api_endpoint="https://work.mercor.com/explore"), Mock())
        raw = conn._build_from_mercor_dict(dict(id="job1", title="Office Assistant", rate=25))
        self.assertEqual(raw.description_text, "")
        self.assertFalse(classify_relevance(raw).is_relevant)
        self.assertIsNone(parse_compensation(raw.compensation_raw).unit)

    def test_pay_amount_period_and_unknowns(self):
        pay = parse_compensation("", "Rate: 7.50 USD/hour.")
        self.assertEqual((pay.amount_min, pay.unit), (7.5, "hourly"))
        self.assertEqual(parse_compensation("$120000").amount_max, 120000)
        self.assertIsNone(parse_compensation("$120000").unit)
        task = parse_compensation("", "Paid per approved asset")
        self.assertEqual((task.amount_max, task.currency, task.unit), (None, None, "per_task"))

    def test_malformed_application_urls_and_tracking_duplicates(self):
        for url in ("https://example.com:no/job", "https://[bad/job", "https://user:pass@example.com/job"):
            self.assertFalse(sanitize_official_apply_url(url)[1])
        self.assertEqual(sanitize_official_apply_url("https://example.com/job?utm_source=abc"),
                         sanitize_official_apply_url("https://example.com/job"))

    def test_listed_history_never_closes_on_filter_change_or_reappears_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config, feed = root / "sources.json", root / "feed.json"
            config.write_text(json.dumps({"sources": [dict(id="lever_test", platform="Test",
                official_url="https://example.com", method="lever_api")]}))
            connector = Mock()
            job = listing("Bilingual Hindi AI Trainer", "Remote", "Accepts applicants worldwide. Native fluency in Hindi.",
                          official_posted_at="2022-01-01")
            with patch("ai_jobs_v2.pipeline.create_connector", return_value=connector):
                def run(day):
                    pipeline = JobDiscoveryPipeline(config, root / "state", feed)
                    summary = pipeline.run(f"2026-11-{day:02d}T00:00:00Z")["summary"]
                    result = json.loads(feed.read_text())
                    self.assertTrue(validate_android_feed_payload(result)[0])
                    return result["jobs"], summary, pipeline.history_store.records["lever_test:123"]

                connector.fetch_listings.return_value = [job]
                jobs, _, record = run(1)
                self.assertEqual(jobs[0]["eligible"], "Remote Global")
                self.assertIn("Native fluency in Hindi", jobs[0]["requirementsEvidence"])
                first_seen = record["first_detected_at"]
                job.description_text = "Must live in Canada."
                jobs, counts, record = run(2)
                self.assertEqual((jobs, counts["deduplication"]["closedJobs"]), ([], 0))
                job.description_text = "This is not an active job opening."
                jobs, _, record = run(3)
                self.assertEqual(record["withheld_reason"], "talent_pool")
                connector.fetch_listings.side_effect = RuntimeError("temporary failure")
                jobs, _, record = run(4)
                self.assertEqual(jobs, [])
                self.assertEqual(record["first_detected_at"], first_seen)
                connector.fetch_listings.side_effect = None
                job.is_explicitly_closed = True
                jobs, counts, _ = run(5)
                self.assertEqual((jobs, counts["deduplication"]["closedJobs"]), ([], 1))
