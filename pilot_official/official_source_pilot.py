import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from ai_jobs_v2.models import RawJobListing
from ai_jobs_v2.http_client import PoliteHTTPClient
from ai_jobs_v2.normalizer import normalize_raw_listing, normalize_iso_date

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    ("Meridial", "greenhouse", "agency"),
    ("LILT", "ashby", "lilt-production"),
    ("Anyone AI", "ashby", "anyone-ai"),
    ("Handshake", "ashby", "handshake"),
    ("Terac", "ashby", "terac"),
]

def key(url):
    p = urlsplit(url or "")
    return (p.netloc.lower(), p.path.rstrip("/")) if p.scheme == "https" and p.netloc and p.path.strip("/") else None

def age_hours(value, now):
    try:
        normalized = normalize_iso_date(value)
        if not normalized:
            return None
        age = (now - datetime.fromisoformat(normalized.replace("Z", "+00:00"))).total_seconds()/3600
        return age if age >= 0 else None
    except (ValueError, OverflowError, OSError):
        return None

def posting(item, ats):
    return item.get("first_published") if ats == "greenhouse" else item.get("publishedAt")

def main():
    now = datetime.now(timezone.utc)
    baseline = json.loads((ROOT/"staging/freshness_catalog.json").read_text())
    seen = {k for j in baseline["jobs"] for k in (key(j.get("applyUrl")), key(j.get("officialSourceUrl"))) if k}
    http = PoliteHTTPClient(max_retries=0)
    reports = []
    for platform, ats, board in SOURCES:
        endpoint = (f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true" if ats == "greenhouse"
                    else f"https://api.ashbyhq.com/posting-api/job-board/{board}")
        report = dict(platform=platform, ats=ats, board=board, endpoint=endpoint, permission="UNVERIFIED_RESEARCH_ONLY",
                      raw=0, relevant=0, incremental_exact_url=0, recent24h=0, recent7d=0, undated=0, duplicate_exact_url=0,
                      malformed_url=0, samples=[], status="FAILED")
        try:
            response = http.get(endpoint, timeout_sec=20, rate_limit_rps=1)
            report["http_status"] = response.status_code
            payload = response.json()
            jobs = payload.get("jobs")
            if not isinstance(jobs, list):
                raise ValueError("Expected jobs array")
            report["raw"] = len(jobs)
            report["status"] = "SUCCESS" if jobs else "EMPTY"
            for item in jobs:
                if not isinstance(item, dict) or item.get("isListed") is False or not item.get("id") or not item.get("title"):
                    continue
                loc = item.get("location") or ""
                if isinstance(loc, dict):
                    loc = loc.get("name", "")
                url = item.get("absolute_url") or item.get("jobUrl") or ""
                apply = item.get("applyUrl") or url
                raw = RawJobListing(source_connector=f"pilot_{ats}_{board}", platform=platform,
                    source_job_id=str(item["id"]), title=item["title"], official_source_url=url, raw_apply_url=apply,
                    location_raw=loc, description_text=item.get("content") or item.get("descriptionPlain") or item.get("descriptionHtml") or "",
                    official_posted_at=posting(item, ats), employment_type=item.get("employmentType") or "")
                record, _, _ = normalize_raw_listing(raw, now.isoformat())
                if record is None:
                    continue
                report["relevant"] += 1
                keys = {k for k in (key(url), key(apply)) if k}
                if not keys or not record.apply_url_available:
                    report["malformed_url"] += 1
                    continue
                if keys & seen:
                    report["duplicate_exact_url"] += 1
                    continue
                seen.update(keys)
                report["incremental_exact_url"] += 1
                age = age_hours(posting(item, ats), now)
                if age is None:
                    report["undated"] += 1
                else:
                    report["recent24h"] += int(age <= 24)
                    report["recent7d"] += int(age <= 168)
                if len(report["samples"]) < 5:
                    report["samples"].append(dict(title=record.title, official_date=record.official_posted_date, apply_url=record.apply_url))
        except Exception as exc:
            report["status"] = "FAILED"
            report["error"] = str(exc)[:500]
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False))
    output = dict(experimentalOnly=True, doNotPublishToAndroid=True, generated_at=now.isoformat(),
                  limitations=["Exact URL matching only; semantic duplicates require review", "URL syntax only; live Apply page not verified", "Board ownership and reuse permission require independent verification", "Ashby publishedAt may represent republication"], sources=reports)
    Path("official-source-pilot-report.json").write_text(json.dumps(output, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
