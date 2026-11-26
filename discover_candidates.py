#!/usr/bin/env python3
"""CLI Entrypoint for AI Jobs Hub v2.1 Career-Ops Inspired Discovery Pilot.

Generates `staging/discovery_candidates.json` and `state/DISCOVERY_PILOT_REPORT.md`
while enforcing byte-for-byte immutability on `staging/jobs_feed.json`,
`jobs_feed.json`, and `state/job_history.json`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pathlib
import sys
from typing import Dict, Optional

ROOT_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR / "src"))

from ai_jobs_v2.discovery_pilot import CareerOpsDiscoveryPilot, validate_pilot_candidates_payload


def _file_sha256(path: pathlib.Path) -> Optional[str]:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AI Jobs Hub v2.1 – Experimental Public-Provider Discovery Pilot (Isolated from Android Feed)"
    )
    parser.add_argument(
        "--pilot-config",
        default=str(ROOT_DIR / "config/discovery_pilot_sources.json"),
        help="Path to experimental discovery pilot sources config JSON.",
    )
    parser.add_argument(
        "--staging-feed",
        default=str(ROOT_DIR / "staging/jobs_feed.json"),
        help="Read-only path to established Android v2 staging feed for overlap deduplication.",
    )
    parser.add_argument(
        "--state-dir",
        default=str(ROOT_DIR / "state"),
        help="Path to state directory (reads existing history/coverage; writes separate pilot report).",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT_DIR / "staging/discovery_candidates.json"),
        help="Target path for isolated experimental candidate feed.",
    )
    parser.add_argument(
        "--validate-pilot",
        default="",
        help="Validate an existing discovery_candidates.json file and exit.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.validate_pilot:
        target = pathlib.Path(args.validate_pilot)
        if not target.exists():
            logging.error("Pilot candidates file does not exist: %s", target)
            return 1
        payload = json.loads(target.read_text(encoding="utf-8"))
        ok, errs = validate_pilot_candidates_payload(payload)
        if not ok:
            for err in errs:
                logging.error("Pilot validation error: %s", err)
            return 1
        logging.info(
            "Pilot candidates validation PASSED (verified=%d, unverified=%d): %s",
            len(payload.get("verifiedEligibleCandidates", [])),
            len(payload.get("unverifiedCandidates", [])),
            target,
        )
        return 0

    out_path = pathlib.Path(args.output).resolve()
    staging_feed_path = pathlib.Path(args.staging_feed).resolve()
    prod_feed_path = (ROOT_DIR / "jobs_feed.json").resolve()
    state_dir = pathlib.Path(args.state_dir).resolve()
    history_path = (state_dir / "job_history.json").resolve()

    if out_path in (staging_feed_path, prod_feed_path):
        logging.error("SAFETY VIOLATION: Refusing to overwrite Android or production jobs_feed.json")
        return 2

    protected_paths: Dict[str, Optional[str]] = {
        str(staging_feed_path): _file_sha256(staging_feed_path),
        str(prod_feed_path): _file_sha256(prod_feed_path),
        str(history_path): _file_sha256(history_path),
    }

    pilot = CareerOpsDiscoveryPilot(
        pilot_config_path=pathlib.Path(args.pilot_config),
        staging_feed_path=staging_feed_path,
        state_dir=state_dir,
        output_candidates_path=out_path,
    )
    payload = pilot.run()
    ok, errs = validate_pilot_candidates_payload(payload)
    if not ok:
        for err in errs:
            logging.error("Generated pilot payload failed validation: %s", err)
        return 1

    for p_str, before_hash in protected_paths.items():
        after_hash = _file_sha256(pathlib.Path(p_str))
        if before_hash != after_hash:
            logging.error("SAFETY VIOLATION: Protected file changed during pilot run: %s", p_str)
            return 2

    summary = payload["summary"]
    logging.info(
        "Discovery pilot finished: %d/%d providers OK (%d failed) | Raw=%d | RelevantAI=%d (CoreEval=%d, BroadAI=%d) | Recent7d=%d | VerifiedID=%d | Unverified=%d | OverlapDirect=%d",
        summary["providersSucceeded"],
        summary["providersConfigured"],
        summary["providersFailed"],
        summary["rawRecordsRetrieved"],
        summary["uniqueRelevantAiRecords"],
        summary["aiEvalCoreRecords"],
        summary["aiMlBroadRecords"],
        summary["postedWithin7DaysRecords"],
        summary["verifiedIndonesiaEligibleRecords"],
        summary["unresolvedEligibilityRecords"],
        summary["overlapWithExistingDirectSources"],
    )
    if summary["providersSucceeded"] == 0 and summary["providersConfigured"] > 0:
        logging.error("All experimental discovery providers failed.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
