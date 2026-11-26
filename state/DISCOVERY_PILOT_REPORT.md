# AI Jobs Hub v2.1 — Career-Ops Inspired Discovery Pilot Report

- **Generated At (UTC):** `2026-11-26T07:03:12Z`
- **Mode:** Isolated Experimental Discovery (`staging/discovery_candidates.json` — Android `staging/jobs_feed.json` untouched)
- **Upstream Reference:** `santifer/career-ops` (`career-ops-hq/career-ops`, MIT License, Copyright (c) 2026 Santiago Fernández de Valderrama)

## Source & Provider Comparison

| Source / Provider | Status | Raw Records | Unique Relevant AI Records (Core Eval / Broad AI) | Recent (<= 7d Posted) | Verified Eligible | Unverified / Unresolved | Overlap w/ Direct | Restricted Excluded | Collection Failures |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **Existing Direct Backend (11/14 active)** | `BASELINE` | 1940 | 917 (917 / 0) | 1 (feed: 0) | 23 (20 ID + 3 Global) | 131 | - | - | 3 |
| **Himalayas** (`pilot_himalayas`) | `SUCCESS` | 346 | 63 (55 / 8) | 63 | 3 | 4 | 0 | 56 | 0 |
| **Remotive** (`pilot_remotive`) | `SUCCESS` | 16 | 2 (2 / 0) | 1 | 0 | 0 | 0 | 2 | 0 |
| **Jobicy** (`pilot_jobicy`) | `SUCCESS` | 1428 | 104 (64 / 40) | 104 | 0 | 3 | 0 | 101 | 0 |
| **Pilot Total (Deduplicated Net-New)** | `3/3 OK` | 1790 | 169 (121 / 48) | 168 | 3 | 7 | 0 (+0 cross-board) | 159 | 0 |

## Provider Diagnostics & Licensing Notes

- **Himalayas (`himalayas_public_api`, `opaque_cursor_plus_filtered_search`, `11504 ms`)**: metadata=`{"expiredSkipped": 0, "pagesFetched": 12, "stopReason": "page_cap_12", "targetedSearchAdded": 106, "truncated": true}` — Free public JSON API (no key required; cached up to 1-2h). Must use applicationLink unchanged for Apply actions, display visible Himalayas credit/link-back, remove expired jobs, and never syndicate to third-party aggregators, Google Jobs, or LinkedIn Jobs.
- **Remotive (`remotive_public_api`, `single_full_feed`, `237 ms`)**: metadata=`{"legalNoticePresent": true, "pagesFetched": 1, "reportedJobCount": 16, "stopReason": "complete", "truncated": false}` — Free public JSON API (no key required; 24-hour listing delay). Rate-limited above 2 req/min (max 4 requests/day recommended). Must link back to the Remotive listing URL and credit Remotive; prohibited from republishing to third-party job boards or gating behind email signup.
- **Jobicy (`jobicy_public_api`, `opaque_cursor_7d_window`, `29669 ms`)**: metadata=`{"friendlyNoticePresent": true, "pagesFetched": 15, "stopReason": "complete", "truncated": false}` — Free public JSON API returning remote listings published in the last 7 days (~3h delay; 1 req/s IP limit, fair-use <= 1 automated pass/hour). Public url points to Jobicy listing page (direct ATS URLs require paid Commercial API key); attribution and link-back to Jobicy required.
