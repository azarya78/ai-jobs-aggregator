#!/usr/bin/env python3
"""CLI Entrypoint for AI Jobs Hub v2 Direct Platform Job Discovery Pipeline."""
from __future__ import annotations
import argparse
import json
import logging
import pathlib
import sys

ROOT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from ai_jobs_v2.pipeline import JobDiscoveryPipeline
from ai_jobs_v2.validator import validate_android_feed_payload


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Jobs Hub v2 — Direct Platform Job Collector")
    parser.add_argument(
        "--mode",
        choices=["staging", "production"],
        default="staging",
        help="Target output feed: 'staging' writes staging/jobs_feed.json (safe default); 'production' writes root jobs_feed.json.",
    )
    parser.add_argument(
        "--sources",
        default=str(ROOT_DIR / "config/sources.json"),
        help="Path to declarative sources registry JSON.",
    )
    parser.add_argument(
        "--state-dir",
        default=str(ROOT_DIR / "state"),
        help="Directory for persistent job_history.json, http_cache.json, and coverage reports.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional explicit output path override.",
    )
    parser.add_argument(
        "--boolean-eligible",
        action="store_true",
        help="Emit boolean true for 'eligible' field if legacy Android build expects boolean instead of string label.",
    )
    parser.add_argument(
        "--validate-only",
        default="",
        help="Validate an existing jobs_feed.json file against the Android schema and exit.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.validate_only:
        target = pathlib.Path(args.validate_only)
        if not target.exists():
            logging.error("Feed file does not exist: %s", target)
            return 1
        payload = json.loads(target.read_text(encoding="utf-8"))
        ok, errs = validate_android_feed_payload(payload)
        if not ok:
            for e in errs:
                logging.error("Validation error: %s", e)
            return 1
        logging.info("Feed validation PASSED (%d jobs): %s", payload["totalJobs"], target)
        return 0

    if args.output:
        out_path = pathlib.Path(args.output)
    elif args.mode == "production":
        out_path = ROOT_DIR / "jobs_feed.json"
    else:
        out_path = ROOT_DIR / "staging/jobs_feed.json"

    pipeline = JobDiscoveryPipeline(
        sources_config_path=pathlib.Path(args.sources),
        state_dir=pathlib.Path(args.state_dir),
        output_feed_path=out_path,
        boolean_eligible=args.boolean_eligible,
    )
    report = pipeline.run()
    summary = report["summary"]
    logging.info(
        "Collection finished: %d/%d sources succeeded (%d failed) | Published=%d (ID=%d, Global=%d) | ReviewQueue=%d",
        summary["sourcesSucceeded"],
        summary["totalSourcesConfigured"],
        summary["sourcesFailed"],
        summary["publishedAndroidFeedTotal"],
        summary["indonesiaEligibleListings"],
        summary["remoteGlobalListings"],
        summary["unverifiedReviewListings"],
    )

    if summary["sourcesSucceeded"] == 0 and summary["totalSourcesConfigured"] > 0:
        logging.error("All configured connectors failed; aborting publication.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
