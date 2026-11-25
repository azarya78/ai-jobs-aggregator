"""Deterministic unit & regression test suite for AI Jobs Hub v2.

Compatible with both:
  - `pytest tests/ -v --tb=short` (GitHub Actions Gate 1, with beautifulsoup4 installed)
  - `PYTHONPATH=src python3 -m unittest discover -s tests -v` (stdlib runner)
"""
from __future__ import annotations
import importlib.util
import json
import pathlib
import tempfile
import unittest

from ai_jobs_v2.models import (
    RawJobListing,
    NormalizedJobRecord,
    CompensationInfo,
    ELIGIBILITY_INDONESIA,
    ELIGIBILITY_GLOBAL,
    ELIGIBILITY_RESTRICTED,
    ELIGIBILITY_UNVERIFIED,
    STATUS_NEW,
    STATUS_EXISTING,
    STATUS_UPDATED,
    STATUS_CLOSED,
    STATUS_UNVERIFIED,
)
from ai_jobs_v2.http_client import PoliteHTTPClient, HTTPResponseData, redact_secrets
from ai_jobs_v2.url_sanitizer import sanitize_official_apply_url
from ai_jobs_v2.compensation import parse_compensation
from ai_jobs_v2.relevance import classify_relevance
from ai_jobs_v2.eligibility import classify_eligibility
from ai_jobs_v2.history import JobHistoryStore
from ai_jobs_v2.validator import validate_android_feed_payload, write_json_atomically
from ai_jobs_v2.reporter import build_coverage_report_dict, render_coverage_markdown
from ai_jobs_v2.connectors.lever import LeverConnector
from ai_jobs_v2.connectors.greenhouse import GreenhouseConnector
from ai_jobs_v2.connectors.ashby import AshbyConnector
from ai_jobs_v2.connectors.smartrecruiters import SmartRecruitersConnector

HAS_BS4 = importlib.util.find_spec("bs4") is not None


class TestAPIConnectorsAndPagination(unittest.TestCase):
    def test_lever_pagination_empty_page_and_malformed_record_skip(self):
        calls = []

        def fake_transport(url, headers, params, timeout):
            calls.append(dict(params or {}))
            skip = (params or {}).get("skip", 0)
            if skip == 0:
                page1 = [
                    {
                        "id": "lev-id-1",
                        "text": "Crux - Video and Audio Raters - Indonesian (Indonesia)",
                        "categories": {"location": "Remote", "commitment": "Freelance"},
                        "country": "Indonesia",
                        "workplaceType": "remote",
                        "descriptionPlain": "Evaluate short video and audio clips in Bahasa Indonesia.",
                        "salaryRange": {"min": 4, "max": 4, "currency": "USD", "interval": "hour"},
                        "hostedUrl": "https://jobs.lever.co/weloglobal/lev-id-1",
                        "applyUrl": "https://jobs.lever.co/weloglobal/lev-id-1/apply",
                        "createdAt": 1763337600000,
                    },
                    {"id": "malformed-empty-title", "text": ""},
                ]
                return HTTPResponseData(200, json.dumps(page1), {}, url)
            return HTTPResponseData(200, "[]", {}, url)

        client = PoliteHTTPClient(transport_override=fake_transport, sleep_fn=lambda _: None)
        conn = LeverConnector(
            {
                "id": "lever_weloglobal",
                "platform": "Welo Data (Welocalize)",
                "official_url": "https://jobs.lever.co/weloglobal",
                "api_endpoint": "https://api.lever.co/v0/postings/weloglobal",
                "method": "lever_api",
                "pure_ai_eval_board": True,
            },
            client,
        )
        conn.PAGE_SIZE = 2
        listings = conn.fetch_listings()
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["skip"], 0)
        self.assertEqual(calls[1]["skip"], 2)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].source_job_id, "lev-id-1")
        self.assertIn("Indonesia", listings[0].location_raw)

    def test_greenhouse_ashby_and_smartrecruiters_connectors(self):
        def fake_transport(url, headers, params, timeout):
            if "greenhouse.io" in url:
                body = {
                    "jobs": [
                        {
                            "id": 4500442005,
                            "title": "Penulis Lepas Bahasa Indonesia",
                            "location": {"name": "Remote - Indonesia"},
                            "content": "Memberi peringkat pada serangkaian respons AI.",
                            "first_published": "2026-11-20T10:00:00Z",
                            "absolute_url": "https://job-boards.greenhouse.io/outlier/jobs/4500442005",
                            "pay_input_ranges": [{"min_cents": 750, "max_cents": 1500, "currency_type": "USD"}],
                        }
                    ]
                }
                return HTTPResponseData(200, json.dumps(body), {}, url)
            if "ashbyhq.com" in url:
                body = {
                    "jobs": [
                        {
                            "id": "ash-1",
                            "title": "AI Rubric Auditor",
                            "isListed": True,
                            "isRemote": True,
                            "location": "Remote - Worldwide",
                            "descriptionPlain": "Evaluate LLM responses for factuality. Accepts applicants worldwide.",
                            "compensation": {"compensationTierSummary": "$35 - $50 / hr"},
                            "publishedAt": "2026-11-22T00:00:00Z",
                            "jobUrl": "https://jobs.ashbyhq.com/afterquery/ash-1",
                        },
                        {"id": "ash-hidden", "title": "Unlisted Role", "isListed": False},
                    ]
                }
                return HTTPResponseData(200, json.dumps(body), {}, url)
            if url.endswith("/Appen/postings"):
                body = {
                    "totalFound": 1,
                    "content": [
                        {
                            "id": "sr-101",
                            "name": "Indonesian Search Engine Evaluator",
                            "location": {"city": "Jakarta", "country": "Indonesia", "remote": True},
                            "releasedDate": "2026-11-18T08:00:00Z",
                        }
                    ],
                }
                return HTTPResponseData(200, json.dumps(body), {}, url)
            if url.endswith("/Appen/postings/sr-101"):
                detail = {
                    "postingUrl": "https://jobs.smartrecruiters.com/Appen/sr-101",
                    "applyUrl": "https://jobs.smartrecruiters.com/Appen/sr-101",
                    "jobAd": {"sections": {"jobDescription": {"text": "Evaluate search engine results in Indonesia."}}},
                }
                return HTTPResponseData(200, json.dumps(detail), {}, url)
            raise RuntimeError(f"Unhandled URL: {url}")

        client = PoliteHTTPClient(transport_override=fake_transport, sleep_fn=lambda _: None)
        gh = GreenhouseConnector(
            {
                "id": "greenhouse_outlier",
                "platform": "Outlier",
                "official_url": "https://job-boards.greenhouse.io/outlier",
                "api_endpoint": "https://boards-api.greenhouse.io/v1/boards/outlier/jobs?content=true",
                "canonical_apply_template": "https://app.outlier.ai/opportunities/{job_id}",
                "method": "greenhouse_api",
                "pure_ai_eval_board": True,
            },
            client,
        ).fetch_listings()
        self.assertEqual(len(gh), 1)
        self.assertEqual(gh[0].raw_apply_url, "https://app.outlier.ai/opportunities/4500442005")

        ash = AshbyConnector(
            {
                "id": "ashby_afterquery",
                "platform": "AfterQuery",
                "official_url": "https://jobs.ashbyhq.com/afterquery",
                "api_endpoint": "https://api.ashbyhq.com/posting-api/job-board/afterquery?includeCompensation=true",
                "method": "ashby_api",
            },
            client,
        ).fetch_listings()
        self.assertEqual(len(ash), 1)
        self.assertEqual(ash[0].source_job_id, "ash-1")

        sr = SmartRecruitersConnector(
            {
                "id": "smartrecruiters_appen",
                "platform": "Appen / CrowdGen",
                "official_url": "https://careers.smartrecruiters.com/Appen",
                "api_endpoint": "https://api.smartrecruiters.com/v1/companies/Appen/postings",
                "method": "smartrecruiters_api",
            },
            client,
        ).fetch_listings()
        self.assertEqual(len(sr), 1)
        self.assertIn("Indonesia", sr[0].location_raw)


class TestHTTPResilienceAndSecurity(unittest.TestCase):
    def test_conditional_etag_retry_403_non_bypass_and_secret_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = pathlib.Path(tmp) / "http_cache.json"
            attempts = {"count": 0}

            def fake_transport(url, headers, params, timeout):
                attempts["count"] += 1
                if attempts["count"] == 1:
                    return HTTPResponseData(503, "Service Unavailable", {}, url)
                if headers.get("If-None-Match") == '"v1-etag"':
                    return HTTPResponseData(304, "", {"etag": '"v1-etag"'}, url)
                return HTTPResponseData(200, '[{"ok": true}]', {"etag": '"v1-etag"'}, url)

            c1 = PoliteHTTPClient(cache_path=cache_file, max_retries=2, transport_override=fake_transport, sleep_fn=lambda _: None)
            r1 = c1.get("https://api.lever.co/v0/postings/rws")
            self.assertEqual(r1.status_code, 200)
            self.assertFalse(r1.not_modified)
            c1.save_cache()

            c2 = PoliteHTTPClient(cache_path=cache_file, max_retries=2, transport_override=fake_transport, sleep_fn=lambda _: None)
            r2 = c2.get("https://api.lever.co/v0/postings/rws")
            self.assertTrue(r2.not_modified)
            self.assertEqual(r2.json(), [{"ok": True}])

            # Confirm HTTP 403 is never retried (strict anti-bot/auth compliance)
            forbid_calls = {"n": 0}
            def forbid_transport(url, headers, params, timeout):
                forbid_calls["n"] += 1
                return HTTPResponseData(403, "Forbidden", {}, url)

            c_forbid = PoliteHTTPClient(max_retries=3, transport_override=forbid_transport, sleep_fn=lambda _: None)
            with self.assertRaises(RuntimeError) as ctx:
                c_forbid.get("https://example.com/private?token=MY_SECRET_999")
            self.assertEqual(forbid_calls["n"], 1)
            self.assertNotIn("MY_SECRET_999", str(ctx.exception))
            self.assertIn("[REDACTED]", redact_secrets("url?api_key=xyz987"))


class TestCompensationRelevanceEligibilityAndURLs(unittest.TestCase):
    def test_compensation_preserves_units_without_hourly_inference(self):
        empty = parse_compensation("", "Flexible schedule evaluating search results.")
        self.assertIsNone(empty.amount_min)
        self.assertIsNone(empty.unit)
        self.assertEqual(empty.raw_text, "")

        hourly = parse_compensation("Up to $25 USD/hr")
        self.assertIsNone(hourly.amount_min)
        self.assertEqual(hourly.amount_max, 25.0)
        self.assertEqual(hourly.currency, "USD")
        self.assertEqual(hourly.unit, "hourly")

        per_task = parse_compensation("$98 - $150 per task")
        self.assertEqual(per_task.amount_min, 98.0)
        self.assertEqual(per_task.amount_max, 150.0)
        self.assertEqual(per_task.unit, "per_task")

        per_project = parse_compensation("$300 (one-time)")
        self.assertEqual(per_project.amount_max, 300.0)
        self.assertEqual(per_project.unit, "per_project")

    def test_relevance_includes_ai_eval_and_excludes_corporate_roles(self):
        swe = RawJobListing("ashby_afterquery", "AfterQuery", "1", "Senior Software Engineer", "https://a.com", "https://a.com", description_text="Build AI evaluation infra.")
        self.assertFalse(classify_relevance(swe).is_relevant)

        recruiter = RawJobListing("teamtailor_dataforce", "DataForce", "733603", "Freelance Local Recruiter", "https://a.com", "https://a.com", description_text="Recruit annotators.")
        self.assertFalse(classify_relevance(recruiter).is_relevant)

        fitness = RawJobListing("web_mercor", "Mercor", "fit1", "iPhone Users: Earn Money for Sharing Your Fitness Data", "https://work.mercor.com/jobs/fit1", "https://work.mercor.com/jobs/fit1", description_text="Share workouts.")
        self.assertFalse(classify_relevance(fitness).is_relevant)

        codename = RawJobListing("lever_weloglobal", "Welo Data", "crux-9", "Project Sirius", "https://jobs.lever.co/weloglobal/crux-9", "https://jobs.lever.co/weloglobal/crux-9", description_text="Contributors evaluate machine translated text and rank AI responses.", pure_ai_eval_board=True)
        rel = classify_relevance(codename)
        self.assertTrue(rel.is_relevant)

    def test_eligibility_indonesia_global_restricted_and_unverified(self):
        # Indonesian language role restricted to US & Canada residents -> Restricted (zero false positive!)
        us_locked = RawJobListing(
            "greenhouse_outlier",
            "Outlier",
            "4421856005",
            "Voice Conversation Evaluator - Indonesian",
            "https://job-boards.greenhouse.io/outlier/jobs/4421856005",
            "https://app.outlier.ai/opportunities/4421856005",
            location_raw="Remote - Canada, United States",
            description_text="Fluent in Bahasa Indonesia. Open to residents of the US and Canada.",
        )
        res_us = classify_eligibility(us_locked)
        self.assertEqual(res_us.classification, ELIGIBILITY_RESTRICTED)
        self.assertTrue(res_us.is_indonesian_language)

        # APAC without Indonesia confirmation -> Unverified
        apac = RawJobListing("lever_rws", "RWS", "1", "AI Data Specialist (APAC)", "https://jobs.lever.co/rws/1", "https://jobs.lever.co/rws/1", location_raw="Remote - APAC", description_text="Evaluate LLM outputs across APAC.")
        self.assertEqual(classify_eligibility(apac).classification, ELIGIBILITY_UNVERIFIED)

        # Explicit Indonesia -> Indonesia Eligible
        id_ok = RawJobListing("teamtailor_dataforce", "DataForce", "736980", "SME Task Creator – Indonesia", "https://community.transperfect.com/jobs/736980", "https://community.transperfect.com/jobs/736980", location_raw="Remote", description_text="Work Location: Remote in Indonesia.")
        self.assertEqual(classify_eligibility(id_ok).classification, ELIGIBILITY_INDONESIA)

        # Explicit Worldwide -> Remote Global
        glob_ok = RawJobListing("web_mindrift", "Mindrift", "md-1", "AI Factuality Tutor", "https://mindrift.ai/apply", "https://mindrift.ai/apply", location_raw="Remote", description_text="Accepts applicants worldwide to verify LLM factuality.")
        self.assertEqual(classify_eligibility(glob_ok).classification, ELIGIBILITY_GLOBAL)

    def test_official_url_sanitization_blocks_remowork_and_referrals(self):
        clean, ok, _ = sanitize_official_apply_url(
            "https://remowork.life/jobs/welocalize-123?ref=abc",
            fallback_official_url="https://jobs.lever.co/weloglobal/uuid-1/apply?utm_source=remowork&referring_user=xyz&lever-origin=applied",
        )
        self.assertTrue(ok)
        self.assertEqual(clean, "https://jobs.lever.co/weloglobal/uuid-1/apply?lever-origin=applied")

        clean2, ok2, note2 = sanitize_official_apply_url("https://bit.ly/shortened-job")
        self.assertFalse(ok2)
        self.assertEqual(clean2, "")
        self.assertIn("blocked", note2)


class TestPersistentHistoryAndAndroidValidator(unittest.TestCase):
    def _make_rec(self, cid: str, sid: str, apply_url: str, desc_hash: str = "h1", posted: str = None) -> NormalizedJobRecord:
        return NormalizedJobRecord(
            canonical_id=cid,
            source_job_id=sid,
            title="Online Data Analyst - Indonesia",
            company="TELUS Digital AI",
            logo="https://www.google.com/s2/favicons?domain=telusdigital.com&sz=128",
            official_source_url=apply_url,
            apply_url=apply_url,
            apply_url_available=True,
            category="Search Quality Rating",
            skills=["Indonesian", "Search Rating"],
            location="Jakarta, Indonesia",
            eligibility_class=ELIGIBILITY_INDONESIA,
            eligibility_confidence="high",
            eligibility_evidence="Jakarta, Indonesia",
            is_indonesian=True,
            is_direct=True,
            compensation=CompensationInfo(),
            short_description="Evaluate search queries in Indonesia.",
            full_description_hash=desc_hash,
            official_posted_date=posted,
            first_detected_at="2026-11-25T00:00:00Z",
            last_verified_at="2026-11-25T00:00:00Z",
            job_status=STATUS_NEW,
            source_connector="web_telus_ai",
            collected_at="2026-11-25T00:00:00Z",
        )

    def test_persistent_history_dedup_fault_hold_and_android_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = pathlib.Path(tmp)
            hist_path = tmp_p / "job_history.json"
            feed_path = tmp_p / "jobs_feed.json"

            # Run 1: Duplicate apply_url collapsed; NEW job recorded; missing officialPostedDate preserved as None
            store1 = JobHistoryStore(hist_path)
            r1 = self._make_rec("web_telus_ai:Req1", "Req1", "https://jobs.telusdigital.com/job/1", desc_hash="h1", posted=None)
            r1_dup = self._make_rec("web_telus_ai:Req1_mirror", "Req1_mirror", "https://jobs.telusdigital.com/job/1", desc_hash="h1", posted=None)
            active1, sum1 = store1.reconcile([r1, r1_dup], set(), {"web_telus_ai"}, set(), "2026-11-25T00:00:00Z")
            store1.save_atomic("2026-11-25T00:00:00Z")
            self.assertEqual(sum1.duplicates_collapsed, 1)
            self.assertEqual(sum1.new_count, 1)
            self.assertEqual(len(active1), 1)
            self.assertIsNone(active1[0].official_posted_date)

            # Validate Android feed serialization (both default string & --boolean-eligible)
            payload = {
                "lastUpdated": "2026-11-25T00:00:00Z",
                "totalJobs": 1,
                "jobs": [active1[0].to_android_feed_item(boolean_eligible=False)],
            }
            write_json_atomically(feed_path, payload, validate_android_schema=True)
            loaded_feed = json.loads(feed_path.read_text(encoding="utf-8"))
            ok, errs = validate_android_feed_payload(loaded_feed)
            self.assertTrue(ok, errs)
            self.assertEqual(loaded_feed["jobs"][0]["date"], "2026-11-25")

            # Run 2: Connector fails -> job MUST become UNVERIFIED (not CLOSED) and preserve firstDetectedAt
            store2 = JobHistoryStore(hist_path)
            active2, sum2 = store2.reconcile([], set(), set(), {"web_telus_ai"}, "2026-11-25T04:00:00Z")
            store2.save_atomic("2026-11-25T04:00:00Z")
            self.assertEqual(sum2.closed_count, 0)
            self.assertEqual(sum2.unverified_connector_hold_count, 1)
            self.assertEqual(len(active2), 1)
            self.assertEqual(active2[0].job_status, STATUS_UNVERIFIED)
            self.assertEqual(active2[0].first_detected_at, "2026-11-25T00:00:00Z")

            # Run 3: Connector recovers with updated description hash -> UPDATED, preserving firstDetectedAt
            store3 = JobHistoryStore(hist_path)
            r3 = self._make_rec("web_telus_ai:Req1", "Req1", "https://jobs.telusdigital.com/job/1", desc_hash="h2_changed", posted=None)
            active3, sum3 = store3.reconcile([r3], set(), {"web_telus_ai"}, set(), "2026-11-25T08:00:00Z")
            store3.save_atomic("2026-11-25T08:00:00Z")
            self.assertEqual(sum3.updated_count, 1)
            self.assertEqual(active3[0].job_status, STATUS_UPDATED)
            self.assertEqual(active3[0].first_detected_at, "2026-11-25T00:00:00Z")

            # Run 4: Connector succeeds with 0 listings -> job marked CLOSED
            store4 = JobHistoryStore(hist_path)
            active4, sum4 = store4.reconcile([], set(), {"web_telus_ai"}, set(), "2026-11-25T12:00:00Z")
            self.assertEqual(sum4.closed_count, 1)
            self.assertEqual(len(active4), 0)

    def test_atomic_validator_blocks_remowork_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_target = pathlib.Path(tmp) / "jobs_feed.json"
            bad_payload = {
                "lastUpdated": "2026-11-25T12:00:00Z",
                "totalJobs": 1,
                "jobs": [
                    {
                        "id": "bad:1",
                        "title": "Test Role",
                        "company": "Test",
                        "logo": "",
                        "category": "LLM Evaluation",
                        "eligible": "Indonesia Eligible",
                        "isIndonesian": True,
                        "isDirect": False,
                        "skills": [],
                        "location": "Indonesia",
                        "pay": "",
                        "date": "2026-11-25",
                        "timestamp": 1764072000000,
                        "excerpt": "Test",
                        "applyUrl": "https://remowork.life/job/123",
                        "source": "remowork",
                    }
                ],
            }
            with self.assertRaises(ValueError):
                write_json_atomically(bad_target, bad_payload, validate_android_schema=True)
            self.assertFalse(bad_target.exists())


class TestEndToEndJobDiscoveryPipeline(unittest.TestCase):
    def test_full_pipeline_multi_run_lifecycle_and_rss_normalizer(self):
        from ai_jobs_v2.pipeline import JobDiscoveryPipeline

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = pathlib.Path(tmp)
            cfg_path = tmp_p / "sources.json"
            cfg_path.write_text(
                json.dumps(
                    {
                        "version": "2.0.0",
                        "sources": [
                            {
                                "id": "teamtailor_dataforce",
                                "platform": "DataForce by TransPerfect",
                                "official_url": "https://community.transperfect.com/jobs",
                                "api_endpoint": "https://community.transperfect.com/jobs.rss",
                                "method": "teamtailor_feed",
                                "enabled": True,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            def fake_transport(url, headers, params, timeout):
                rss = """<?xml version="1.0" encoding="UTF-8"?>
                <rss version="2.0"><channel>
                  <item>
                    <title>SME Task Creator – Indonesia</title>
                    <link>https://community.transperfect.com/jobs/736980-sme-indonesia</link>
                    <description>&lt;p&gt;Work Location: Remote in Indonesia. Create realistic work tasks and materials for AI training.&lt;/p&gt;</description>
                    <pubDate>Fri, 20 Nov 2026 12:00:00 GMT</pubDate>
                  </item>
                </channel></rss>"""
                return HTTPResponseData(200, rss, {}, url)

            client = PoliteHTTPClient(transport_override=fake_transport, sleep_fn=lambda _: None)
            feed_file = tmp_p / "staging/jobs_feed.json"
            cov = JobDiscoveryPipeline(cfg_path, tmp_p / "state", feed_file, http_client=client).run("2026-11-25T12:00:00Z")
            self.assertEqual(cov["summary"]["publishedAndroidFeedTotal"], 1)
            self.assertEqual(cov["summary"]["indonesiaEligibleListings"], 1)
            feed_json = json.loads(feed_file.read_text(encoding="utf-8"))
            ok, errs = validate_android_feed_payload(feed_json)
            self.assertTrue(ok, errs)
            self.assertNotIn("<p>", feed_json["jobs"][0]["excerpt"])
            self.assertTrue((tmp_p / "state/COVERAGE_REPORT.md").exists())
            self.assertTrue((tmp_p / "state/daily_new_candidates.json").exists())


class TestEmptyPublicHTMLFaultHold(unittest.TestCase):
    def test_empty_html_extraction_does_not_close_existing_job(self):
        from unittest.mock import patch
        from ai_jobs_v2.pipeline import JobDiscoveryPipeline

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            config = root / "sources.json"
            config.write_text(json.dumps({"sources": [{
                "id": "web_mercor",
                "platform": "Mercor",
                "official_url": "https://work.mercor.com/explore",
                "method": "mercor_public_web",
                "enabled": True
            }]}), encoding="utf-8")
            output = root / "staging/jobs_feed.json"
            job = RawJobListing(
                "web_mercor", "Mercor", "list_123",
                "Indonesian AI Response Evaluator",
                "https://work.mercor.com/jobs/list_123",
                "https://work.mercor.com/jobs/list_123",
                location_raw="Indonesia",
                description_text="Evaluate and grade AI model responses.",
            )

            class FakeConnector:
                def __init__(self, listings):
                    self.listings = listings

                def fetch_listings(self):
                    return self.listings

            with patch("ai_jobs_v2.pipeline.create_connector", return_value=FakeConnector([job])):
                first = JobDiscoveryPipeline(config, root / "state", output).run("2026-11-25T00:00:00Z")
            self.assertEqual(first["summary"]["publishedAndroidFeedTotal"], 1)

            with patch("ai_jobs_v2.pipeline.create_connector", return_value=FakeConnector([])):
                second = JobDiscoveryPipeline(config, root / "state", output).run("2026-11-25T04:00:00Z")
            self.assertEqual(second["sources"][0]["status"], "FAILED")
            self.assertEqual(second["summary"]["deduplication"]["closedJobs"], 0)
            self.assertEqual(second["summary"]["publishedAndroidFeedTotal"], 1)
            self.assertEqual(second["summary"]["deduplication"]["unverifiedHeldDueToConnectorFailure"], 1)


if __name__ == "__main__":
    unittest.main()
