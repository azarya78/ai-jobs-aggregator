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
from ai_jobs_v2.connectors.teamtailor import TeamtailorConnector

HAS_BS4 = importlib.util.find_spec("bs4") is not None


class TestEligibilityReclassificationHistory(unittest.TestCase):
    def test_previously_eligible_review_job_is_not_closed(self):
        from ai_jobs_v2.history import JobHistoryStore
        from ai_jobs_v2.normalizer import normalize_raw_listing

        with tempfile.TemporaryDirectory() as d:
            store = JobHistoryStore(pathlib.Path(d) / "history.json")
            def make(location, description):
                raw = RawJobListing(
                    "lever_weloglobal", "Welo Data", "job-123", "AI Review Lead",
                    "https://jobs.lever.co/weloglobal/job-123",
                    "https://jobs.lever.co/weloglobal/job-123/apply",
                    location_raw=location, description_text=description,
                )
                job, _, _ = normalize_raw_listing(raw, collected_at_iso="2026-11-25T00:00:00Z")
                return job

            original = make("Indonesia · ID · remote", "Evaluate AI outputs in Indonesia.")
            self.assertIsNotNone(original)
            feed, first = store.reconcile([original], set(), {"lever_weloglobal"}, set(), "2026-11-25T00:00:00Z")
            self.assertEqual(len(feed), 1)
            self.assertEqual(first.new_count, 1)

            review = make("Croatia · HR · remote", "Countries: Australia, Indonesia, Germany. Evaluate AI outputs.")
            self.assertIsNotNone(review)
            self.assertEqual(review.eligibility_class, ELIGIBILITY_UNVERIFIED)
            feed, second = store.reconcile([], set(), {"lever_weloglobal"}, set(), "2026-11-26T00:00:00Z", unverified_records=[review])
            self.assertEqual(len(feed), 0)
            self.assertEqual(second.closed_count, 0)
            self.assertEqual(store.records[original.canonical_id]["job_status"], STATUS_UNVERIFIED)
            self.assertEqual(store.records[original.canonical_id]["eligibility_class"], ELIGIBILITY_UNVERIFIED)

            # Historical false closure can be repaired when still present in source.
            store.records[original.canonical_id]["job_status"] = STATUS_CLOSED
            feed, third = store.reconcile([], set(), {"lever_weloglobal"}, set(), "2026-11-27T00:00:00Z", unverified_records=[review])
            self.assertEqual(third.closed_count, 0)
            self.assertEqual(store.records[original.canonical_id]["job_status"], STATUS_UNVERIFIED)


class TestConflictingGeographicEvidence(unittest.TestCase):
    def test_broad_employer_country_list_does_not_override_foreign_location(self):
        description = (
            "About our company: available in countries: Argentina, Australia, "
            "Brazil, Germany, Indonesia, Japan. Join our global network."
        )
        for title, location in [
            ("English to Belarusian Review Lead", "Remote - Europe · GB · remote"),
            ("English to Bosnian Review Lead", "Croatia · HR · remote"),
            ("English to Estonian Review Lead", "Greece · GR · remote"),
            ("English to German Review Lead", "Zurich, Switzerland · CH · remote"),
        ]:
            with self.subTest(location=location):
                job = RawJobListing(
                    "lever_weloglobal", "Welo Data", "sample-id", title,
                    "https://jobs.lever.co/weloglobal/example",
                    "https://jobs.lever.co/weloglobal/example",
                    location_raw=location, description_text=description,
                )
                result = classify_eligibility(job)
                self.assertEqual(result.classification, ELIGIBILITY_UNVERIFIED)
                self.assertIn("Conflicting geographic signals", result.evidence)

    def test_country_list_with_indonesia_location_remains_eligible(self):
        job = RawJobListing(
            "web_oneforma", "OneForma", "sample-id", "Translation Quality Rater",
            "https://www.oneforma.com/projects/example",
            "https://www.oneforma.com/projects/example",
            location_raw="Available in 32 countries Germany India Indonesia Remote Spain",
            description_text="Evaluate translation quality with structured rubrics.",
        )
        self.assertEqual(classify_eligibility(job).classification, ELIGIBILITY_INDONESIA)

    def test_role_specific_indonesia_permission_overrides_foreign_location(self):
        job = RawJobListing(
            "lever_weloglobal", "Welo Data", "sample-id", "AI Response Evaluator",
            "https://jobs.lever.co/weloglobal/example",
            "https://jobs.lever.co/weloglobal/example",
            location_raw="United Kingdom · GB · remote",
            description_text=(
                "We are hiring candidates based in Indonesia for remote AI QA. "
                "Company operations span countries: Australia, Indonesia, UK."
            ),
        )
        self.assertEqual(classify_eligibility(job).classification, ELIGIBILITY_INDONESIA)


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
                        "categories": {"location": "Remote", "allLocations": ["Remote", "Bandung"], "commitment": "Freelance"},
                        "country": "Indonesia",
                        "workplaceType": "remote",
                        "descriptionPlain": "Evaluate short video and audio clips in Bahasa Indonesia.",
                        "salaryRange": {"min": 4, "max": 4, "currency": "USD", "interval": "per-hour-wage"},
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
        self.assertIn("Bandung", listings[0].location_raw)
        self.assertIn("Indonesia", listings[0].location_raw)
        self.assertEqual(listings[0].compensation_raw, "USD 4 / hour")

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
                "official_url": "https://app.outlier.ai/opportunities",
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

    def test_http_304_with_empty_or_bodyless_cache_refetches_unconditionally(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = pathlib.Path(tmp) / "http_cache.json"
            cache_file.write_text(
                json.dumps({"https://api.lever.co/v0/postings/weloglobal": {"etag": '"stale"', "body": ""}}),
                encoding="utf-8",
            )
            seen_headers = []

            def fake_transport(url, headers, params, timeout):
                seen_headers.append(dict(headers))
                if "If-None-Match" in headers:
                    return HTTPResponseData(304, "", {"etag": '"stale"'}, url)
                return HTTPResponseData(200, '[{"id": "job1"}]', {"etag": '"fresh"'}, url)

            client = PoliteHTTPClient(cache_path=cache_file, transport_override=fake_transport, sleep_fn=lambda _: None)
            resp = client.get("https://api.lever.co/v0/postings/weloglobal")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), [{"id": "job1"}])
            self.assertNotIn("If-None-Match", seen_headers[0])


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

        lever_wage = parse_compensation("USD 5 / per-hour-wage")
        self.assertEqual(lever_wage.amount_min, 5.0)
        self.assertEqual(lever_wage.unit, "hourly")
        self.assertEqual(lever_wage.raw_text, "USD 5 / hour")

        fallback_trimmed = parse_compensation("", "Compensation: 5 USD/hour Help Shape the Future of AI This short project offers")
        self.assertEqual(fallback_trimmed.amount_min, 5.0)
        self.assertEqual(fallback_trimmed.unit, "hourly")
        self.assertEqual(fallback_trimmed.raw_text, "5 USD/hour")

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

        email_study = RawJobListing("web_oneforma", "OneForma", "em1", "Online Shopping Email Collection: Part-Time Remote Work From Home", "https://www.oneforma.com/projects/em1/", "https://www.oneforma.com/projects/em1/", description_text="Share order and delivery emails from your own inbox.", pure_ai_eval_board=True)
        self.assertFalse(classify_relevance(email_study).is_relevant)

        codename = RawJobListing("lever_weloglobal", "Welo Data", "crux-9", "Project Sirius", "https://jobs.lever.co/weloglobal/crux-9", "https://jobs.lever.co/weloglobal/crux-9", description_text="Contributors evaluate machine translated text and rank AI responses.", pure_ai_eval_board=True)
        rel = classify_relevance(codename)
        self.assertTrue(rel.is_relevant)

    def test_eligibility_indonesia_global_restricted_and_unverified(self):
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

        apac = RawJobListing("lever_rws", "RWS", "1", "AI Data Specialist (APAC)", "https://jobs.lever.co/rws/1", "https://jobs.lever.co/rws/1", location_raw="Remote - APAC", description_text="Evaluate LLM outputs across APAC.")
        self.assertEqual(classify_eligibility(apac).classification, ELIGIBILITY_UNVERIFIED)

        id_ok = RawJobListing("teamtailor_dataforce", "DataForce", "736980", "SME Task Creator - Indonesia", "https://community.transperfect.com/jobs/736980", "https://community.transperfect.com/jobs/736980", location_raw="Remote", description_text="Work Location: Remote in Indonesia.")
        self.assertEqual(classify_eligibility(id_ok).classification, ELIGIBILITY_INDONESIA)

        glob_ok = RawJobListing("web_mindrift", "Mindrift", "md-1", "AI Factuality Tutor", "https://mindrift.ai/apply", "https://mindrift.ai/apply", location_raw="Remote", description_text="Accepts applicants worldwide to verify LLM factuality.")
        self.assertEqual(classify_eligibility(glob_ok).classification, ELIGIBILITY_GLOBAL)

    def test_eligibility_audit_regressions_iso_codes_cities_and_global_precedence(self):
        for loc_sample in ("Slovakia · SK · remote", "Iceland · IS · remote", "Kyiv · UA · remote"):
            rec = RawJobListing("lever_weloglobal", "Welo Data", "eu1", "Ads Quality Rater", "https://jobs.lever.co/weloglobal/1", "https://jobs.lever.co/weloglobal/1", location_raw=loc_sample, description_text="Rate ads.")
            self.assertEqual(classify_eligibility(rec).classification, ELIGIBILITY_RESTRICTED, loc_sample)

        appen_us = RawJobListing(
            "smartrecruiters_appen",
            "Appen",
            "743999694507495",
            "Internet Analyst for Bilingual Speakers",
            "https://jobs.smartrecruiters.com/Appen/743999694507495",
            "https://jobs.smartrecruiters.com/Appen/743999694507495",
            location_raw="Tysons, VA, us",
            description_text="Partnering with top 10 global technology companies across North America and Asia Pacific to improve search for users worldwide.",
        )
        self.assertEqual(classify_eligibility(appen_us).classification, ELIGIBILITY_RESTRICTED)

        chicago = RawJobListing("web_alignerr", "Alignerr", "al1", "Native Spanish Speaker: Healthcare AI Voice Conversations (Chicago)", "https://www.alignerr.com/jobs/al1", "https://www.alignerr.com/jobs/al1", location_raw="Remote", description_text="Voice recording.")
        vienna = RawJobListing("web_alignerr", "Alignerr", "al2", "German Workspace AI Reviewer - Vienna", "https://www.alignerr.com/jobs/al2", "https://www.alignerr.com/jobs/al2", location_raw="Remote", description_text="Review AI outputs.")
        self.assertEqual(classify_eligibility(chicago).classification, ELIGIBILITY_RESTRICTED)
        self.assertEqual(classify_eligibility(vienna).classification, ELIGIBILITY_RESTRICTED)

        of_id = RawJobListing("web_oneforma", "OneForma", "of1", "Paragraph-Level Translation Quality Rater", "https://www.oneforma.com/projects/of1/", "https://www.oneforma.com/projects/of1/", location_raw="Available in 12 countries Czech Republic Germany Iceland India Indonesia Spain United States", description_text="Evaluate translation quality.")
        of_no_id = RawJobListing("web_oneforma", "OneForma", "of2", "Local Search Quality Evaluator", "https://www.oneforma.com/projects/of2/", "https://www.oneforma.com/projects/of2/", location_raw="", description_text="Available in 6 countries China Mexico Norway South Korea United Kingdom United States")
        self.assertEqual(classify_eligibility(of_id).classification, ELIGIBILITY_INDONESIA)
        self.assertEqual(classify_eligibility(of_no_id).classification, ELIGIBILITY_RESTRICTED)

        worldwide_with_asia = RawJobListing("web_mercor", "Mercor", "m1", "AI Evaluation Rubric Auditor", "https://work.mercor.com/jobs/m1", "https://work.mercor.com/jobs/m1", location_raw="Remote", description_text="Open to applicants worldwide across the Americas, Europe, and Asia.")
        self.assertEqual(classify_eligibility(worldwide_with_asia).classification, ELIGIBILITY_GLOBAL)

        bare_remote = RawJobListing("web_alignerr", "Alignerr", "al3", "Customer Support & Success Expert: AI Workflow Review", "https://www.alignerr.com/jobs/al3", "https://www.alignerr.com/jobs/al3", location_raw="Remote", description_text="Evaluate AI customer support workflows.")
        self.assertEqual(classify_eligibility(bare_remote).classification, ELIGIBILITY_UNVERIFIED)

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

            store1 = JobHistoryStore(hist_path)
            r1 = self._make_rec("web_telus_ai:Req1", "Req1", "https://jobs.telusdigital.com/job/1", desc_hash="h1", posted=None)
            r1_dup = self._make_rec("web_telus_ai:Req1_mirror", "Req1_mirror", "https://jobs.telusdigital.com/job/1", desc_hash="h1", posted=None)
            active1, sum1 = store1.reconcile([r1, r1_dup], set(), {"web_telus_ai"}, set(), "2026-11-25T00:00:00Z")
            store1.save_atomic("2026-11-25T00:00:00Z")
            self.assertEqual(sum1.duplicates_collapsed, 1)
            self.assertEqual(sum1.new_count, 1)
            self.assertEqual(len(active1), 1)
            self.assertIsNone(active1[0].official_posted_date)

            feed_item = active1[0].to_android_feed_item(boolean_eligible=False)
            self.assertIsNone(feed_item["officialPostedDate"])
            self.assertEqual(feed_item["firstDetectedAt"], "2026-11-25T00:00:00Z")
            self.assertEqual(feed_item["firstSeenAt"], "2026-11-25T00:00:00Z")

            payload = {
                "lastUpdated": "2026-11-25T00:00:00Z",
                "totalJobs": 1,
                "jobs": [feed_item],
            }
            write_json_atomically(feed_path, payload, validate_android_schema=True)
            loaded_feed = json.loads(feed_path.read_text(encoding="utf-8"))
            ok, errs = validate_android_feed_payload(loaded_feed)
            self.assertTrue(ok, errs)
            self.assertEqual(loaded_feed["jobs"][0]["date"], "2026-11-25")

            store2 = JobHistoryStore(hist_path)
            active2, sum2 = store2.reconcile([], set(), set(), {"web_telus_ai"}, "2026-11-25T04:00:00Z")
            store2.save_atomic("2026-11-25T04:00:00Z")
            self.assertEqual(sum2.closed_count, 0)
            self.assertEqual(sum2.unverified_connector_hold_count, 1)
            self.assertEqual(len(active2), 1)
            self.assertEqual(active2[0].job_status, STATUS_UNVERIFIED)
            self.assertEqual(active2[0].first_detected_at, "2026-11-25T00:00:00Z")

            store3 = JobHistoryStore(hist_path)
            r3 = self._make_rec("web_telus_ai:Req1", "Req1", "https://jobs.telusdigital.com/job/1", desc_hash="h2_changed", posted=None)
            active3, sum3 = store3.reconcile([r3], set(), {"web_telus_ai"}, set(), "2026-11-25T08:00:00Z")
            store3.save_atomic("2026-11-25T08:00:00Z")
            self.assertEqual(sum3.updated_count, 1)
            self.assertEqual(active3[0].job_status, STATUS_UPDATED)
            self.assertEqual(active3[0].first_detected_at, "2026-11-25T00:00:00Z")

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
    <title>SME Task Creator - Indonesia</title>
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

    def test_anomalous_source_wide_drop_holds_previously_verified_jobs(self):
        from unittest.mock import patch
        from ai_jobs_v2.pipeline import JobDiscoveryPipeline

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            config = root / "sources.json"
            config.write_text(
                json.dumps(
                    {
                        "sources": [
                            {
                                "id": "lever_weloglobal",
                                "platform": "Welo Data",
                                "official_url": "https://jobs.lever.co/weloglobal",
                                "api_endpoint": "https://api.lever.co/v0/postings/weloglobal",
                                "method": "lever_api",
                                "enabled": True,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            output = root / "staging/jobs_feed.json"
            initial_jobs = [
                RawJobListing(
                    "lever_weloglobal",
                    "Welo Data",
                    f"id-{i}",
                    f"Search Quality Rater - Indonesia {i}",
                    f"https://jobs.lever.co/weloglobal/id-{i}",
                    f"https://jobs.lever.co/weloglobal/id-{i}/apply",
                    location_raw="Indonesia · ID · remote",
                    description_text="Evaluate search queries in Bahasa Indonesia.",
                )
                for i in range(3)
            ]

            class FakeConnector:
                def __init__(self, listings):
                    self.listings = listings

                def fetch_listings(self):
                    return self.listings

            with patch("ai_jobs_v2.pipeline.create_connector", return_value=FakeConnector(initial_jobs)):
                r1 = JobDiscoveryPipeline(config, root / "state", output).run("2026-11-25T00:00:00Z")
                self.assertEqual(r1["summary"]["publishedAndroidFeedTotal"], 3)

            with patch("ai_jobs_v2.pipeline.create_connector", return_value=FakeConnector([])):
                r2 = JobDiscoveryPipeline(config, root / "state", output).run("2026-11-25T04:00:00Z")
                self.assertEqual(r2["sources"][0]["status"], "FAILED")
                self.assertEqual(r2["summary"]["deduplication"]["closedJobs"], 0)
                self.assertEqual(r2["summary"]["deduplication"]["unverifiedHeldDueToConnectorFailure"], 3)
                self.assertEqual(r2["summary"]["publishedAndroidFeedTotal"], 3)


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


class TestMercorPublicBoard(unittest.TestCase):
    def test_mercor_source_targets_explore_and_extracts_public_job_card(self):
        from ai_jobs_v2.connectors.public_web import MercorConnector
        project_root = pathlib.Path(__file__).resolve().parents[1]
        registry = json.loads((project_root / "config/sources.json").read_text(encoding="utf-8"))
        cfg = next(s for s in registry["sources"] if s["id"] == "web_mercor")
        self.assertEqual(cfg["html_endpoint"], "https://work.mercor.com/explore")

        called = []
        html = """
        <html><body><article>
          <a href="/jobs/list_test123">
            <h3>Indonesian AI Response Evaluator</h3>
            Evaluate LLM responses and grade model quality.
          </a>
        </article></body></html>
        """

        def fake_transport(url, headers, params, timeout):
            called.append(url)
            return HTTPResponseData(200, html, {}, url)

        client = PoliteHTTPClient(transport_override=fake_transport, sleep_fn=lambda _: None)
        jobs = MercorConnector(cfg, client).fetch_listings()
        self.assertEqual(called, ["https://work.mercor.com/explore"])
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_job_id, "list_test123")
        self.assertIn("Indonesian AI Response Evaluator", jobs[0].title)
        self.assertEqual(jobs[0].raw_apply_url, "https://work.mercor.com/jobs/list_test123")


@unittest.skipUnless(HAS_BS4, "beautifulsoup4 required for HTML/SSR connector tests")
class TestRecoveredConnectorsSuite(unittest.TestCase):
    def test_mercor_dehydrated_react_query_next_data_and_closed_flag(self):
        from ai_jobs_v2.connectors.public_web import MercorConnector

        next_data = {
            "props": {
                "pageProps": {
                    "dehydratedState": {
                        "queries": [
                            {
                                "state": {
                                    "data": {
                                        "listings": [
                                            {
                                                "listingId": "list_AAABoXlzADizwRyyDutBMpdx",
                                                "slug": "indonesian-linguistic-experts",
                                                "title": "Indonesian Linguistic Experts",
                                                "rateMin": 30,
                                                "rateMax": 30,
                                                "ratePeriod": "hourly",
                                                "location": "Indonesia",
                                                "description": "Evaluate LLM-generated Indonesian text and annotate data.",
                                            },
                                            {
                                                "listingId": "list_closed99",
                                                "slug": "closed-role",
                                                "title": "Bilingual Indonesian Generalist Expert — AI Safety",
                                                "statusText": "no longer accepting applications",
                                                "isClosed": True,
                                            },
                                        ]
                                    }
                                }
                            }
                        ]
                    }
                }
            }
        }
        html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_data)}</script></body></html>'
        client = PoliteHTTPClient(transport_override=lambda u, h, p, t: HTTPResponseData(200, html, {}, u), sleep_fn=lambda _: None)
        conn = MercorConnector(
            {
                "id": "web_mercor",
                "platform": "Mercor",
                "official_url": "https://work.mercor.com/explore",
                "api_endpoint": "https://work.mercor.com/sitemap.xml",
                "html_endpoint": "https://work.mercor.com/explore",
                "method": "mercor_public_web",
            },
            client,
        )
        jobs = conn.fetch_listings()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].raw_apply_url, "https://work.mercor.com/jobs/list_AAABoXlzADizwRyyDutBMpdx/indonesian-linguistic-experts")
        self.assertEqual(jobs[0].compensation_raw, "$30 / hr")
        self.assertFalse(jobs[0].is_explicitly_closed)
        self.assertTrue(jobs[1].is_explicitly_closed)

    def test_micro1_portal_post_api_and_canonical_post_urls(self):
        from ai_jobs_v2.connectors.public_web import Micro1Connector

        portal_payload = {
            "data": {
                "jobs": [
                    {
                        "job_id": "fd2eabd8-79df-4f01-bf92-06cf69af2acd",
                        "job_title": "Bilingual Indonesian Generalists",
                        "skills": ["Indonesian", "Data Annotation"],
                        "min_pay": 30,
                        "max_pay": 65,
                        "location": "Remote - Indonesia",
                        "description": "Develop and refine Indonesian-language training datasets to train frontier AI models with high quality data.",
                        "posted_at": "2026-11-21T00:00:00Z",
                    }
                ]
            }
        }

        def fake_post(url, headers, body, timeout):
            self.assertEqual(url, "https://prod-api.micro1.ai/api/v1/job/portal")
            return HTTPResponseData(200, json.dumps(portal_payload), {}, url)

        client = PoliteHTTPClient(post_transport_override=fake_post, sleep_fn=lambda _: None)
        conn = Micro1Connector(
            {
                "id": "web_micro1",
                "platform": "Micro1",
                "official_url": "https://www.micro1.ai/experts/opportunities",
                "api_endpoint": "https://www.micro1.ai/experts/opportunities",
                "portal_api_endpoint": "https://prod-api.micro1.ai/api/v1/job/portal",
                "method": "micro1_public_web",
            },
            client,
        )
        jobs = conn.fetch_listings()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].raw_apply_url, "https://jobs.micro1.ai/post/fd2eabd8-79df-4f01-bf92-06cf69af2acd")
        self.assertEqual(jobs[0].compensation_raw, "$30 - $65 / hr")
        self.assertEqual(classify_eligibility(jobs[0]).classification, ELIGIBILITY_INDONESIA)

    def test_oneforma_wp_rest_and_paginated_projects_html_merge(self):
        from ai_jobs_v2.connectors.public_web import OneFormaConnector

        wp_json = [
            {
                "id": 901,
                "slug": "paragraph-level-translation-quality-rater",
                "link": "https://www.oneforma.com/projects/paragraph-level-translation-quality-rater/",
                "title": {"rendered": "Paragraph-Level Translation Quality Rater"},
                "content": {"rendered": "<p>Evaluate translation quality for multilingual AI systems.</p>"},
                "date_gmt": "2026-11-20T09:00:00",
            }
        ]
        page1_html = """
        <html><body>
          <article>
            <h3>Paragraph-Level Translation Quality Rater</h3>
            <p>Azerbaijan · Brazil +29 Available in 31 countries Azerbaijan Brazil India Indonesia United States Remote 2 hr per day</p>
            <a href="https://www.oneforma.com/projects/paragraph-level-translation-quality-rater/">Apply</a>
          </article>
          <a href="https://www.oneforma.com/projects/page/2/">2</a>
        </body></html>
        """
        page2_html = """
        <html><body>
          <article>
            <h3>Uzbek Audio Quality Evaluator (Project Echo)</h3>
            <p>Open to applicants in Uzbekistan. $9.20 per hour</p>
            <a href="https://www.oneforma.com/projects/uzbek-audio-quality-evaluator/">Apply</a>
          </article>
        </body></html>
        """

        def fake_get(url, headers, params, timeout):
            if "wp-json/wp/v2/job" in url:
                return HTTPResponseData(200, json.dumps(wp_json), {}, url)
            if url.endswith("/projects/page/2/"):
                return HTTPResponseData(200, page2_html, {}, url)
            return HTTPResponseData(200, page1_html, {}, url)

        client = PoliteHTTPClient(transport_override=fake_get, sleep_fn=lambda _: None)
        conn = OneFormaConnector(
            {
                "id": "web_oneforma",
                "platform": "OneForma (Centific)",
                "official_url": "https://www.oneforma.com/projects/",
                "api_endpoint": "https://www.oneforma.com/wp-json/wp/v2/job?per_page=100&_embed=1",
                "html_endpoint": "https://www.oneforma.com/projects/",
                "method": "oneforma_public_web",
                "pure_ai_eval_board": True,
            },
            client,
        )
        jobs = conn.fetch_listings()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].source_job_id, "901")
        self.assertIn("Indonesia", jobs[0].location_raw)
        self.assertEqual(classify_eligibility(jobs[0]).classification, ELIGIBILITY_INDONESIA)
        self.assertEqual(jobs[1].compensation_raw, "$9.20 per hour")
        self.assertEqual(classify_eligibility(jobs[1]).classification, ELIGIBILITY_RESTRICTED)

    def test_mindrift_workable_widget_deduplicates_shortcodes(self):
        from ai_jobs_v2.connectors.public_web import MindriftConnector

        workable_json = {
            "name": "Mindrift",
            "jobs": [
                {
                    "title": "Freelance AI Tutor - Indonesian",
                    "shortcode": "IND101",
                    "telecommuting": True,
                    "country": "Indonesia",
                    "city": "Jakarta",
                    "published_on": "2026-11-22",
                    "description": "<p>Evaluate AI-generated responses in Bahasa Indonesia.</p>",
                    "locations": [{"country": "Indonesia", "city": "Jakarta", "hidden": False}],
                },
                {
                    "title": "Freelance AI Tutor - Indonesian",
                    "shortcode": "IND101",
                    "telecommuting": True,
                    "country": "Indonesia",
                    "city": "Surabaya",
                    "published_on": "2026-11-22",
                    "description": "<p>Evaluate AI-generated responses in Bahasa Indonesia.</p>",
                    "locations": [{"country": "Indonesia", "city": "Surabaya", "hidden": False}],
                },
            ],
        }
        apply_html = '<html><body><div><h3>Freelance AI Tutor - Indonesian</h3> up to $ 30 /hr Open <a href="https://apply.workable.com/toloka-ai/j/IND101">Apply</a></div></body></html>'

        def fake_get(url, headers, params, timeout):
            if "workable.com" in url:
                return HTTPResponseData(200, json.dumps(workable_json), {}, url)
            return HTTPResponseData(200, apply_html, {}, url)

        client = PoliteHTTPClient(transport_override=fake_get, sleep_fn=lambda _: None)
        conn = MindriftConnector(
            {
                "id": "web_mindrift",
                "platform": "Mindrift (Toloka)",
                "official_url": "https://mindrift.ai/apply",
                "api_endpoint": "https://apply.workable.com/api/v1/widget/accounts/toloka-ai?details=true",
                "html_endpoint": "https://mindrift.ai/apply",
                "method": "mindrift_public_web",
                "pure_ai_eval_board": True,
            },
            client,
        )
        jobs = conn.fetch_listings()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].source_job_id, "IND101")
        self.assertEqual(jobs[0].raw_apply_url, "https://apply.workable.com/toloka-ai/j/IND101")
        self.assertEqual(jobs[0].compensation_raw, "up to $30 /hr")
        self.assertEqual(classify_eligibility(jobs[0]).classification, ELIGIBILITY_INDONESIA)

    def test_imerit_jobs_json_filters_expired_roles_and_preserves_hash_urls(self):
        from ai_jobs_v2.connectors.public_web import IMeritConnector

        jobs_json = [
            {
                "hash": "flynpID999",
                "title": "Indonesian Language Expert",
                "department": "Operations",
                "type": "Independent Contractor (Scholar)",
                "country": "Indonesia",
                "language": "Indonesian",
                "rate": "12",
                "closing_date": "2027-12-31",
                "apply_url": "https://app.scholars.net/signup",
                "overview": "Perform Gen AI data annotations across text and image modalities.",
            },
            {
                "hash": "expiredHash001",
                "title": "Expired Language Expert",
                "country": "Indonesia",
                "closing_date": "2025-01-01",
                "overview": "Perform Gen AI data annotations.",
            },
        ]

        client = PoliteHTTPClient(
            transport_override=lambda u, h, p, t: HTTPResponseData(200, json.dumps(jobs_json), {}, u),
            sleep_fn=lambda _: None,
        )
        conn = IMeritConnector(
            {
                "id": "web_imerit",
                "platform": "iMerit Scholars",
                "official_url": "https://imerit.ai/careers-listing/",
                "api_endpoint": "https://imerit.ai/jobs.json",
                "html_endpoint": "https://imerit.ai/careers-listing/",
                "method": "imerit_public_web",
            },
            client,
        )
        jobs = conn.fetch_listings()
        self.assertEqual(len(jobs), 2)
        self.assertFalse(jobs[0].is_explicitly_closed)
        self.assertEqual(jobs[0].raw_apply_url, "https://imerit.ai/careers-listing/?hash=flynpID999")
        self.assertEqual(jobs[0].compensation_raw, "$12/hr")
        self.assertTrue(jobs[1].is_explicitly_closed)

    def test_telus_official_ssr_indonesia_job_cards(self):
        from ai_jobs_v2.connectors.public_web import TelusAIConnector

        listing_url = "https://jobs.telusdigital.com/jobs/17841045-quality-assurance-rater-indonesian-indonesia"
        html = """
        <html><body><section>
          <article><a href="/jobs/17841045-quality-assurance-rater-indonesian-indonesia">
            Quality Assurance Rater - Indonesian (Indonesia) Jakarta, Indonesia
            &bull; Indonesian &bull; Part time &bull; ReqEG-513 Remote
          </a></article>
          <article><a href="/jobs/1000-it-technician-indonesia">
            IT Technician Denpasar Barat, BA, Indonesia &bull; English &bull; Full time &bull; ReqEG-201 Onsite
          </a></article>
        </section></body></html>"""
        client = PoliteHTTPClient(
            transport_override=lambda url, headers, params, timeout: HTTPResponseData(
                200, html, {}, url
            ),
            sleep_fn=lambda _: None,
        )
        conn = TelusAIConnector({
            "id": "web_telus_ai",
            "platform": "TELUS Digital AI",
            "official_url": "https://jobs.telusdigital.com/search/cfm5/ai-community/jobs/in/country/indonesia",
            "api_endpoint": "https://jobs.telusdigital.com/search/cfm5/ai-community/jobs/in/country/indonesia",
            "method": "telus_ai_public_web",
            "pure_ai_eval_board": True,
        }, client)
        jobs = conn.fetch_listings()
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].raw_apply_url, listing_url)
        self.assertEqual(jobs[0].title, "Quality Assurance Rater - Indonesian (Indonesia)")
        self.assertEqual(jobs[0].location_raw, "Jakarta, Indonesia")
        self.assertEqual(jobs[0].source_job_id, "ReqEG-513")
        self.assertEqual(classify_eligibility(jobs[0]).classification, ELIGIBILITY_INDONESIA)

    def test_outlier_and_telus_report_access_restrictions_without_bypass(self):
        from ai_jobs_v2.connectors.public_web import TelusAIConnector

        def outlier_transport(url, headers, params, timeout):
            if "greenhouse.io" in url:
                return HTTPResponseData(404, "Not Found", {}, url)
            return HTTPResponseData(200, "<html><body>0 Opportunities</body></html>", {}, url)

        gh_client = PoliteHTTPClient(transport_override=outlier_transport, sleep_fn=lambda _: None)
        with self.assertRaises(RuntimeError) as out_ctx:
            GreenhouseConnector(
                {
                    "id": "greenhouse_outlier",
                    "platform": "Outlier",
                    "official_url": "https://app.outlier.ai/opportunities",
                    "api_endpoint": "https://boards-api.greenhouse.io/v1/boards/outlier/jobs?content=true",
                    "public_fallback_url": "https://app.outlier.ai/opportunities",
                    "method": "greenhouse_api",
                },
                gh_client,
            ).fetch_listings()
        self.assertIn("CSRF", str(out_ctx.exception))

        telus_client = PoliteHTTPClient(
            transport_override=lambda u, h, p, t: HTTPResponseData(
                200, "<html><body><noscript>You need to enable JavaScript to run this app.</noscript></body></html>", {}, u
            ),
            sleep_fn=lambda _: None,
        )
        with self.assertRaises(RuntimeError) as telus_ctx:
            TelusAIConnector(
                {
                    "id": "web_telus_ai",
                    "platform": "TELUS Digital AI",
                    "official_url": "https://www.telusinternational.ai/opportunities",
                    "api_endpoint": "https://www.telusinternational.ai/opportunities",
                    "method": "telus_ai_public_web",
                },
                telus_client,
            ).fetch_listings()
        self.assertIn("React SPA shell", str(telus_ctx.exception))

    def test_teamtailor_supplements_html_pages_when_rss_hits_cap(self):
        rss_items = "".join(
            f"<item><title>Role {i}</title><link>https://community.transperfect.com/jobs/{1000 + i}-role</link><description>Work Location: US</description></item>"
            for i in range(2)
        )
        rss_xml = f'<?xml version="1.0"?><rss version="2.0"><channel>{rss_items}</channel></rss>'
        html_p1 = '<html><body><a href="/jobs/1000-role">Role 0</a><a href="/jobs/2001-indonesia-annotator">AI Data Annotator - Indonesia</a><a href="?page=2">Next</a></body></html>'
        html_p2 = '<html><body><a href="/jobs/2001-indonesia-annotator">AI Data Annotator - Indonesia</a></body></html>'
        detail_html = '<html><head><script type="application/ld+json">{"@type":"JobPosting","description":"Work Location: Remote in Indonesia. Annotate datasets.","datePosted":"2026-11-24"}</script></head></html>'

        def fake_get(url, headers, params, timeout):
            if url.endswith(".rss"):
                return HTTPResponseData(200, rss_xml, {}, url)
            if "2001-indonesia-annotator" in url:
                return HTTPResponseData(200, detail_html, {}, url)
            if (params or {}).get("page") == 2:
                return HTTPResponseData(200, html_p2, {}, url)
            return HTTPResponseData(200, html_p1, {}, url)

        client = PoliteHTTPClient(transport_override=fake_get, sleep_fn=lambda _: None)
        conn = TeamtailorConnector(
            {
                "id": "teamtailor_dataforce",
                "platform": "DataForce by TransPerfect",
                "official_url": "https://community.transperfect.com/jobs",
                "api_endpoint": "https://community.transperfect.com/jobs.rss",
                "html_endpoint": "https://community.transperfect.com/jobs",
                "method": "teamtailor_feed",
            },
            client,
        )
        conn.RSS_CAP_THRESHOLD = 2
        jobs = conn.fetch_listings()
        self.assertEqual(len(jobs), 3)
        self.assertEqual(jobs[2].source_job_id, "2001-indonesia-annotator")
        self.assertEqual(jobs[2].location_raw, "Remote in Indonesia")


if __name__ == "__main__":
    unittest.main()
