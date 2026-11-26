"""Regression tests for AI Jobs Hub v2.1 Career-Ops Inspired Discovery Pilot."""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from typing import Dict

from ai_jobs_v2.connectors.career_ops_providers import (
    HimalayasDiscoveryConnector,
    JobicyDiscoveryConnector,
    RemotiveDiscoveryConnector,
    build_https_endpoint,
    validate_allowed_https_url,
)
from ai_jobs_v2.discovery_pilot import (
    CareerOpsDiscoveryPilot,
    ExistingDirectIndex,
    classify_pilot_ai_relevance,
    classify_pilot_eligibility,
    is_posted_within_days,
    validate_pilot_candidates_payload,
)
from ai_jobs_v2.http_client import HTTPResponseData, PoliteHTTPClient
from ai_jobs_v2.models import ELIGIBILITY_INDONESIA, ELIGIBILITY_RESTRICTED, ELIGIBILITY_UNVERIFIED, RawJobListing


def _u(host_parts: tuple[str, ...], path: str) -> str:
    return build_https_endpoint(list(host_parts), path)


class TestCareerOpsProvidersAndPilot(unittest.TestCase):
    def test_himalayas_cursor_pagination_expiry_filter_and_unchanged_apply_link(self) -> None:
        h_browse = _u(("himalayas", "app"), "/jobs/api")
        h_search = _u(("himalayas", "app"), "/jobs/api/search")

        page1 = {
            "totalCount": 3,
            "nextCursor": "CUR_PAGE_2",
            "jobs": [
                {
                    "guid": _u(("himalayas", "app"), "/companies/acme-ai/jobs/indonesian-ai-trainer"),
                    "applicationLink": _u(("himalayas", "app"), "/companies/acme-ai/jobs/indonesian-ai-trainer?src=himalayas"),
                    "title": "Indonesian AI Trainer &amp; Evaluator",
                    "companyName": "Acme AI",
                    "locationRestrictions": ["Indonesia", "Singapore"],
                    "minSalary": 18,
                    "maxSalary": 25,
                    "currency": "USD",
                    "salaryPeriod": "hourly",
                    "pubDate": 1795478400,
                    "expiryDate": 1893456000,
                    "categories": ["Artificial Intelligence"],
                    "excerpt": "Evaluate Bahasa Indonesia LLM responses.",
                    "description": "<p>Evaluate Bahasa Indonesia LLM responses with rubrics.</p>",
                },
                {
                    "guid": _u(("himalayas", "app"), "/companies/old-co/jobs/expired-ai-role"),
                    "applicationLink": _u(("himalayas", "app"), "/companies/old-co/jobs/expired-ai-role"),
                    "title": "Expired AI Annotator",
                    "companyName": "Old Co",
                    "locationRestrictions": [],
                    "pubDate": 1760000000,
                    "expiryDate": 1761000000,
                },
            ],
        }
        page2 = {
            "totalCount": 3,
            "jobs": [
                {
                    "guid": _u(("himalayas", "app"), "/companies/global-labs/jobs/llm-engineer"),
                    "applicationLink": _u(("himalayas", "app"), "/companies/global-labs/jobs/llm-engineer"),
                    "title": "Senior LLM Engineer",
                    "companyName": "Global Labs",
                    "locationRestrictions": [],
                    "minSalary": 1200,
                    "maxSalary": 1500,
                    "currency": "USD",
                    "salaryPeriod": "weekly",
                    "pubDate": None,
                }
            ],
        }
        search_payload = {
            "jobs": [
                page1["jobs"][0],
                {
                    "guid": _u(("himalayas", "app"), "/companies/jakarta-nlp/jobs/data-annotator"),
                    "applicationLink": _u(("himalayas", "app"), "/companies/jakarta-nlp/jobs/data-annotator"),
                    "title": "AI Data Annotator",
                    "companyName": "Jakarta NLP",
                    "locationRestrictions": ["Indonesia"],
                    "minSalary": 12,
                    "maxSalary": 12,
                    "currency": None,
                    "salaryPeriod": None,
                    "pubDate": 1795564800,
                },
            ]
        }

        def fake_transport(url: str, _headers: Dict[str, str], _timeout: int) -> HTTPResponseData:
            if url.startswith(h_search):
                return HTTPResponseData(200, json.dumps(search_payload), {}, url)
            if "cursor=CUR_PAGE_2" in url:
                return HTTPResponseData(200, json.dumps(page2), {}, url)
            return HTTPResponseData(200, json.dumps(page1), {}, url)

        client = PoliteHTTPClient(transport_override=fake_transport, sleep_fn=lambda _: None)
        connector = HimalayasDiscoveryConnector(
            {
                "id": "pilot_himalayas",
                "hostParts": ["himalayas", "app"],
                "maxPages": 5,
                "referenceEpochSec": 1763000000,
                "includeTargetedSearch": True,
                "targetedSearches": [{"country": "Indonesia", "sort": "recent"}],
            },
            client,
        )
        listings = connector.fetch_listings()
        self.assertEqual(len(listings), 3)
        self.assertEqual(connector.last_fetch_metadata["pagesFetched"], 2)
        self.assertEqual(connector.last_fetch_metadata["expiredSkipped"], 1)
        self.assertEqual(connector.last_fetch_metadata["targetedSearchAdded"], 1)

        first = listings[0]
        self.assertEqual(first.title, "Indonesian AI Trainer & Evaluator")
        self.assertTrue(first.raw_apply_url.endswith("?src=himalayas"))
        self.assertEqual(first.extra_metadata["structured_compensation"]["unit"], "hourly")
        self.assertEqual(first.extra_metadata["structured_compensation"]["currency"], "USD")

        weekly = listings[1]
        self.assertIsNone(weekly.extra_metadata["structured_compensation"]["unit"])
        self.assertIsNone(weekly.official_posted_at)

        bare = listings[2]
        self.assertEqual(bare.extra_metadata["structured_compensation"], {})

    def test_remotive_and_jobicy_connectors_decode_entities_and_paginate(self) -> None:
        r_url = _u(("remotive", "com"), "/api/remote-jobs")
        j_url = _u(("jobicy", "com"), "/api/v2/remote-jobs")

        remotive_json = {
            "0-legal-notice": "Link back to Remotive",
            "job-count": 1,
            "jobs": [
                {
                    "id": 9012,
                    "url": _u(("remotive", "com"), "/remote-jobs/ai-ml/prompt-engineer-9012"),
                    "title": "Prompt Engineer &amp; AI Evaluator",
                    "company_name": "Zone &#038; Co",
                    "category": "AI / ML",
                    "job_type": "contract",
                    "publication_date": "2026-11-24T10:30:00",
                    "candidate_required_location": "Worldwide",
                    "salary": "$30 - $45 / hour",
                    "description": "<div>Evaluate LLM prompts and rubrics.</div>",
                }
            ],
        }
        jobicy_p1 = {
            "jobCount": 1,
            "hasMore": True,
            "nextCursor": "J_CUR_2",
            "friendlyNotice": "Credit Jobicy",
            "jobs": [
                {
                    "id": 5501,
                    "url": _u(("jobicy", "com"), "/jobs/5501-ai-trainer"),
                    "jobTitle": "Bilingual AI Trainer &#8211; APAC",
                    "companyName": "Lexi &amp; Labs",
                    "jobIndustry": ["AI &amp; Machine Learning"],
                    "jobType": ["contract"],
                    "jobGeo": "Anywhere",
                    "pubDate": "2026-11-25T08:15:00Z",
                    "salaryMin": 0,
                    "salaryMax": 0,
                    "salaryCurrency": "USD",
                    "salaryPeriod": "hourly",
                    "jobDescription": "<p>Train conversational AI models.</p>",
                }
            ],
        }
        jobicy_p2 = {
            "jobCount": 1,
            "hasMore": False,
            "nextCursor": None,
            "jobs": [
                {
                    "id": 5502,
                    "url": _u(("jobicy", "com"), "/jobs/5502-us-ml-engineer"),
                    "jobTitle": "Machine Learning Engineer",
                    "companyName": "US Cloud Corp",
                    "jobGeo": "USA",
                    "pubDate": "2026-11-23T12:00:00Z",
                    "salaryMin": 140000,
                    "salaryMax": 170000,
                    "salaryCurrency": "USD",
                    "salaryPeriod": "yearly",
                    "jobDescription": "<p>Build ML pipelines.</p>",
                }
            ],
        }

        def fake_transport(url: str, _headers: Dict[str, str], _timeout: int) -> HTTPResponseData:
            if url.startswith(r_url):
                return HTTPResponseData(200, json.dumps(remotive_json), {}, url)
            if "cursor=J_CUR_2" in url:
                return HTTPResponseData(200, json.dumps(jobicy_p2), {}, url)
            if url.startswith(j_url):
                return HTTPResponseData(200, json.dumps(jobicy_p1), {}, url)
            return HTTPResponseData(404, "{}", {}, url)

        client = PoliteHTTPClient(transport_override=fake_transport, sleep_fn=lambda _: None)
        rem_items = RemotiveDiscoveryConnector({"id": "pilot_remotive", "hostParts": ["remotive", "com"]}, client).fetch_listings()
        self.assertEqual(len(rem_items), 1)
        self.assertEqual(rem_items[0].platform, "Zone & Co")
        self.assertEqual(rem_items[0].official_posted_at, "2026-11-24T10:30:00Z")

        jobicy_conn = JobicyDiscoveryConnector({"id": "pilot_jobicy", "hostParts": ["jobicy", "com"]}, client)
        job_items = jobicy_conn.fetch_listings()
        self.assertEqual(len(job_items), 2)
        self.assertEqual(jobicy_conn.last_fetch_metadata["pagesFetched"], 2)
        self.assertEqual(job_items[0].title, "Bilingual AI Trainer – APAC")
        self.assertEqual(job_items[0].extra_metadata["structured_compensation"], {})
        self.assertEqual(job_items[1].extra_metadata["structured_compensation"]["unit"], "annual")

    def test_generic_worldwide_never_counts_as_verified_eligibility(self) -> None:
        ww_raw = RawJobListing(
            source_connector="pilot_remotive",
            platform="Global AI Startup",
            source_job_id="101",
            title="AI Data Trainer",
            official_source_url=_u(("remotive", "com"), "/remote-jobs/ai/101"),
            raw_apply_url=_u(("remotive", "com"), "/remote-jobs/ai/101"),
            location_raw="Worldwide",
            description_text="Open worldwide for remote contributors.",
            extra_metadata={"aggregator_generic_worldwide": True},
        )
        ww_elig = classify_pilot_eligibility(ww_raw)
        self.assertEqual(ww_elig["status"], ELIGIBILITY_UNVERIFIED)
        self.assertFalse(ww_elig["isVerifiedEligible"])
        self.assertEqual(ww_elig["unresolvedReason"], "generic_worldwide_not_proof_of_eligibility")

        apac_raw = RawJobListing(
            source_connector="pilot_jobicy",
            platform="APAC Labs",
            source_job_id="101b",
            title="NLP Engineer",
            official_source_url=_u(("jobicy", "com"), "/jobs/101b"),
            raw_apply_url=_u(("jobicy", "com"), "/jobs/101b"),
            location_raw="APAC",
            description_text="Remote role across Asia-Pacific.",
        )
        apac_elig = classify_pilot_eligibility(apac_raw)
        self.assertEqual(apac_elig["status"], ELIGIBILITY_UNVERIFIED)
        self.assertFalse(apac_elig["isVerifiedEligible"])
        self.assertEqual(apac_elig["unresolvedReason"], "regional_ambiguous_geography")

        id_raw = RawJobListing(
            source_connector="pilot_himalayas",
            platform="Nusantara AI",
            source_job_id="102",
            title="Indonesian LLM Evaluator",
            official_source_url=_u(("himalayas", "app"), "/companies/nusantara/jobs/102"),
            raw_apply_url=_u(("himalayas", "app"), "/companies/nusantara/jobs/102"),
            location_raw="Indonesia",
            description_text="Evaluate Bahasa Indonesia prompts from anywhere in Indonesia.",
            extra_metadata={"location_restrictions": ["Indonesia"]},
        )
        id_elig = classify_pilot_eligibility(id_raw)
        self.assertEqual(id_elig["status"], ELIGIBILITY_INDONESIA)
        self.assertTrue(id_elig["isVerifiedEligible"])

        us_raw = RawJobListing(
            source_connector="pilot_jobicy",
            platform="US Corp",
            source_job_id="103",
            title="AI Trainer",
            official_source_url=_u(("jobicy", "com"), "/jobs/103"),
            raw_apply_url=_u(("jobicy", "com"), "/jobs/103"),
            location_raw="USA",
            description_text="Remote role in the US.",
        )
        us_elig = classify_pilot_eligibility(us_raw)
        self.assertEqual(us_elig["status"], ELIGIBILITY_RESTRICTED)
        self.assertFalse(us_elig["isVerifiedEligible"])

        sales_raw = RawJobListing(
            source_connector="pilot_remotive",
            platform="AI SaaS Co",
            source_job_id="104",
            title="Account Executive - AI Enterprise",
            official_source_url=_u(("remotive", "com"), "/remote-jobs/sales/104"),
            raw_apply_url=_u(("remotive", "com"), "/remote-jobs/sales/104"),
            description_text="Sell our generative AI platform.",
        )
        is_ai, _, _, _, _ = classify_pilot_ai_relevance(sales_raw)
        self.assertFalse(is_ai)

    def test_seven_day_posting_window_never_substitutes_discovery_date(self) -> None:
        run_iso = "2026-11-26T04:30:00Z"
        self.assertFalse(is_posted_within_days(None, run_iso, days=7))
        self.assertTrue(is_posted_within_days("2026-11-26T01:00:00Z", run_iso, days=7))
        self.assertTrue(is_posted_within_days("2026-11-19T05:00:00Z", run_iso, days=7))
        self.assertFalse(is_posted_within_days("2026-11-18T23:59:59Z", run_iso, days=7))

    def test_ats_signature_and_canonical_url_deduplication_against_direct_history(self) -> None:
        idx = ExistingDirectIndex()
        idx.register_record(
            job_id="lever_rws:abc-123",
            company="RWS TrainAI",
            title="AI Voice Recording Contributor",
            apply_url=_u(("jobs", "lever", "co"), "/rws/abc-123/apply"),
            official_source_url=_u(("jobs", "lever", "co"), "/rws/abc-123"),
        )
        matched, jid, reason = idx.match(
            company="Some Reposter",
            title="Different Title",
            apply_url=_u(("jobs", "lever", "co"), "/rws/abc-123"),
            original_source_url=_u(("remotive", "com"), "/remote-jobs/ai/999"),
        )
        self.assertTrue(matched)
        self.assertEqual(jid, "lever_rws:abc-123")
        self.assertIn(reason, ("canonical_url", "ats_posting_id"))

        matched_alias, jid_alias, reason_alias = idx.match(
            company="TrainAI by RWS",
            title="AI Voice Recording Contributor",
            apply_url=_u(("jobicy", "com"), "/jobs/7777"),
            original_source_url=_u(("jobicy", "com"), "/jobs/7777"),
        )
        self.assertTrue(matched_alias)
        self.assertEqual(jid_alias, "lever_rws:abc-123")
        self.assertEqual(reason_alias, "company_and_title")

    def test_repeated_cursor_and_malformed_url_guards(self) -> None:
        root_h = ".".join(("himalayas", "app"))
        self.assertEqual(validate_allowed_https_url("http://" + root_h + "/jobs/1", root_h), "")
        self.assertEqual(validate_allowed_https_url("https" + "://user:sec@" + root_h + "/jobs/1", root_h), "")
        self.assertEqual(validate_allowed_https_url(_u(("evil", "org"), "/jobs/1"), root_h), "")

        loop_payload = {
            "nextCursor": "SAME_CURSOR",
            "jobs": [
                {
                    "guid": _u(("himalayas", "app"), "/companies/c1/jobs/ai-trainer"),
                    "applicationLink": _u(("himalayas", "app"), "/companies/c1/jobs/ai-trainer"),
                    "title": "AI Trainer",
                    "companyName": "C1",
                    "locationRestrictions": [],
                    "pubDate": 1795478400,
                }
            ],
        }
        client = PoliteHTTPClient(
            transport_override=lambda u, _h, _t: HTTPResponseData(200, json.dumps(loop_payload), {}, u),
            sleep_fn=lambda _: None,
        )
        conn = HimalayasDiscoveryConnector({"id": "pilot_himalayas", "hostParts": ["himalayas", "app"], "maxPages": 10}, client)
        rows = conn.fetch_listings()
        self.assertEqual(len(rows), 1)
        self.assertTrue(conn.last_fetch_metadata["truncated"])
        self.assertEqual(conn.last_fetch_metadata["stopReason"], "repeated_cursor_guard")

    def test_end_to_end_pilot_deduplicates_against_direct_sources_and_preserves_staging_feed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            staging_dir = root / "staging"
            state_dir = root / "state"
            config_dir = root / "config"
            staging_dir.mkdir()
            state_dir.mkdir()
            config_dir.mkdir()

            existing_feed = {
                "lastUpdated": "2026-11-25T23:56:05Z",
                "totalJobs": 2,
                "schemaVersion": "2.0",
                "pipeline": "ai-jobs-hub-v2-direct",
                "jobs": [
                    {
                        "id": "lever_weloglobal:existing-1",
                        "title": "Crux - Video and Audio QA - Indonesian",
                        "company": "Welo Data (Welocalize)",
                        "eligibilityClass": "Indonesia Eligible",
                        "officialPostedDate": "2026-11-22T06:00:00Z",
                        "applyUrl": _u(("jobs", "lever", "co"), "/weloglobal/existing-1/apply"),
                        "officialSourceUrl": _u(("jobs", "lever", "co"), "/weloglobal/existing-1"),
                    },
                    {
                        "id": "web_mercor:existing-2",
                        "title": "Generalist AI Safety Rubric Expert",
                        "company": "Mercor",
                        "eligibilityClass": "Remote Global",
                        "officialPostedDate": "2026-09-04T02:01:09Z",
                        "applyUrl": _u(("work", "mercor", "com"), "/jobs/existing-2"),
                        "officialSourceUrl": _u(("work", "mercor", "com"), "/jobs/existing-2"),
                    },
                ],
            }
            staging_feed_path = staging_dir / "jobs_feed.json"
            staging_feed_path.write_text(json.dumps(existing_feed, indent=2), encoding="utf-8")
            before_feed_sha = hashlib.sha256(staging_feed_path.read_bytes()).hexdigest()

            history_path = state_dir / "job_history.json"
            history_path.write_text(
                json.dumps({"schemaVersion": "2.0", "totalTrackedJobs": 2, "jobs": {}}, indent=2),
                encoding="utf-8",
            )
            before_hist_sha = hashlib.sha256(history_path.read_bytes()).hexdigest()

            (state_dir / "coverage_report.json").write_text(
                json.dumps(
                    {
                        "summary": {
                            "totalSourcesConfigured": 14,
                            "sourcesSucceeded": 11,
                            "sourcesFailed": 3,
                            "rawListingsRetrieved": 1928,
                            "relevantAiEvalListings": 909,
                            "unverifiedReviewListings": 131,
                        }
                    }
                ),
                encoding="utf-8",
            )

            pilot_cfg = {
                "version": "2.1.0-pilot",
                "upstreamInspiration": {"project": "santifer/career-ops", "license": "MIT"},
                "providers": [
                    {
                        "id": "pilot_himalayas",
                        "providerKey": "himalayas",
                        "platform": "Himalayas",
                        "connector": "himalayas_public_api",
                        "enabled": True,
                        "hostParts": ["himalayas", "app"],
                        "maxPages": 1,
                    },
                    {
                        "id": "pilot_remotive",
                        "providerKey": "remotive",
                        "platform": "Remotive",
                        "connector": "remotive_public_api",
                        "enabled": True,
                        "hostParts": ["remotive", "com"],
                    },
                    {
                        "id": "pilot_jobicy",
                        "providerKey": "jobicy",
                        "platform": "Jobicy",
                        "connector": "jobicy_public_api",
                        "enabled": True,
                        "hostParts": ["jobicy", "com"],
                        "maxPages": 1,
                    },
                ],
            }
            pilot_cfg_path = config_dir / "discovery_pilot_sources.json"
            pilot_cfg_path.write_text(json.dumps(pilot_cfg), encoding="utf-8")

            h_resp = {
                "jobs": [
                    {
                        "guid": _u(("himalayas", "app"), "/companies/welocalize/jobs/crux-qa"),
                        "applicationLink": _u(("himalayas", "app"), "/companies/welocalize/jobs/crux-qa"),
                        "title": "Crux - Video and Audio QA - Indonesian",
                        "companyName": "Welocalize",
                        "locationRestrictions": ["Indonesia"],
                        "pubDate": 1795478400,
                    },
                    {
                        "guid": _u(("himalayas", "app"), "/companies/archipelago-ai/jobs/id-rlhf-tutor"),
                        "applicationLink": _u(("himalayas", "app"), "/companies/archipelago-ai/jobs/id-rlhf-tutor"),
                        "title": "Indonesian RLHF AI Tutor",
                        "companyName": "Archipelago AI",
                        "locationRestrictions": ["Indonesia"],
                        "minSalary": 15,
                        "maxSalary": 20,
                        "currency": "USD",
                        "salaryPeriod": "hourly",
                        "pubDate": 1795478400,
                    },
                    {
                        "guid": _u(("himalayas", "app"), "/companies/open-llm/jobs/prompt-engineer"),
                        "applicationLink": _u(("himalayas", "app"), "/companies/open-llm/jobs/prompt-engineer"),
                        "title": "Prompt Engineer (LLM)",
                        "companyName": "Open LLM",
                        "locationRestrictions": [],
                        "pubDate": None,
                    },
                ]
            }

            r_resp = {
                "0-legal-notice": "Remotive terms",
                "jobs": [
                    {
                        "id": 777,
                        "url": _u(("remotive", "com"), "/remote-jobs/ai/prompt-engineer-777"),
                        "title": "Prompt Engineer (LLM)",
                        "company_name": "Open LLM",
                        "publication_date": "2026-11-24T12:00:00",
                        "candidate_required_location": "Worldwide",
                        "description": "<p>Design prompts for LLMs.</p>",
                    },
                    {
                        "id": 778,
                        "url": _u(("remotive", "com"), "/remote-jobs/ai/applied-ai-engineer-778"),
                        "title": "Applied AI Engineer",
                        "company_name": "VectorForge",
                        "publication_date": "2026-11-23T09:00:00",
                        "candidate_required_location": "Worldwide",
                        "salary": "$40 - $60 / hour",
                        "description": "<p>Build RAG and LLM evaluation pipelines.</p>",
                    },
                ],
            }

            def fake_transport(url: str, _headers: Dict[str, str], _timeout: int) -> HTTPResponseData:
                if "himalayas" in url:
                    return HTTPResponseData(200, json.dumps(h_resp), {}, url)
                if "remotive" in url:
                    return HTTPResponseData(200, json.dumps(r_resp), {}, url)
                return HTTPResponseData(503, "Service Unavailable", {}, url)

            client = PoliteHTTPClient(transport_override=fake_transport, sleep_fn=lambda _: None)
            out_candidates = staging_dir / "discovery_candidates.json"

            pilot = CareerOpsDiscoveryPilot(
                pilot_config_path=pilot_cfg_path,
                staging_feed_path=staging_feed_path,
                state_dir=state_dir,
                output_candidates_path=out_candidates,
                http_client=client,
            )
            result = pilot.run(run_timestamp_iso="2026-11-26T04:30:00Z")

            ok, errs = validate_pilot_candidates_payload(result)
            self.assertTrue(ok, f"Validation failed: {errs}")

            self.assertEqual(before_feed_sha, hashlib.sha256(staging_feed_path.read_bytes()).hexdigest())
            self.assertEqual(before_hist_sha, hashlib.sha256(history_path.read_bytes()).hexdigest())

            summary = result["summary"]
            self.assertEqual(summary["providersConfigured"], 3)
            self.assertEqual(summary["providersSucceeded"], 2)
            self.assertEqual(summary["providersFailed"], 1)
            self.assertEqual(summary["collectionFailures"], 1)
            self.assertEqual(summary["overlapWithExistingDirectSources"], 1)
            self.assertEqual(summary["crossProviderDuplicatesCollapsed"], 1)
            self.assertEqual(summary["verifiedIndonesiaEligibleRecords"], 1)
            self.assertEqual(summary["unresolvedEligibilityRecords"], 2)

            open_llm = next(c for c in result["unverifiedCandidates"] if c["company"] == "Open LLM")
            self.assertIsNone(open_llm["postingDate"])
            self.assertFalse(open_llm["postedWithin7Days"])
            self.assertEqual(open_llm["discoveryDate"], "2026-11-26T04:30:00Z")

            result_second = pilot.run(run_timestamp_iso="2026-11-27T04:30:00Z")
            open_llm_second = next(c for c in result_second["unverifiedCandidates"] if c["company"] == "Open LLM")
            self.assertEqual(open_llm_second["discoveryDate"], "2026-11-26T04:30:00Z")
            self.assertEqual(open_llm_second["lastCheckedAt"], "2026-11-27T04:30:00Z")

    def test_cli_refuses_to_overwrite_protected_android_or_production_feeds(self) -> None:
        cli_script = pathlib.Path(__file__).resolve().parents[1] / "discover_candidates.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            staging_feed = root / "jobs_feed.json"
            staging_feed.write_text('{"totalJobs": 23, "jobs": []}', encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    str(cli_script),
                    "--staging-feed",
                    str(staging_feed),
                    "--output",
                    str(staging_feed),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertEqual(staging_feed.read_text(encoding="utf-8"), '{"totalJobs": 23, "jobs": []}')


if __name__ == "__main__":
    unittest.main()
