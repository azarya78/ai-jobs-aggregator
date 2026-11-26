"""AITrainer integration candidate audit; no publication, no official-date inference."""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from ai_jobs_v2.http_client import PoliteHTTPClient
from ai_jobs_v2.models import RawJobListing
from ai_jobs_v2.normalizer import normalize_raw_listing
from ai_jobs_v2.url_sanitizer import sanitize_official_apply_url

ROOT = Path(__file__).resolve().parents[1]
ALIASES = {"micro1":"Micro1","turing":"Turing","ethos":"Ethos","meridial":"Meridial","outlier":"Outlier","telus":"TELUS Digital AI","mercor":"Mercor","mindrift":"Mindrift (Toloka)","rws":"RWS TrainAI","alignerr":"Alignerr","innodata":"Innodata","afterquery":"AfterQuery","sme_careers":"SME Careers","anyoneai":"Anyone AI","babelaudio":"Babel Audio"}
def text_field(value):
    return value.strip() if isinstance(value,str) else ""
def url_key(value):
    clean, ok, _ = sanitize_official_apply_url(text_field(value))
    if not ok:
        return None
    p = urlsplit(clean)
    return (p.hostname, p.path.rstrip("/"), tuple(sorted(__import__("urllib.parse",fromlist=["parse_qsl"]).parse_qsl(p.query))))
def candidate(row, now):
    platform = text_field(row.get("platform"))
    title = text_field(row.get("title"))
    sid = text_field(row.get("id")) or str(row.get("id") or "")
    apply = text_field(row.get("apply_url"))
    if not platform or not title or not sid or not url_key(apply):
        return None
    raw = RawJobListing(source_connector="syndicated_aitrainer", platform=ALIASES.get(platform.casefold(),platform), source_job_id=sid, title=title, official_source_url="",raw_apply_url=apply,description_text=text_field(row.get("description")), official_posted_at=None)
    record, _, _ = normalize_raw_listing(raw, now)
    if record:
        record.is_direct = False
    return record

def audit(payload, baseline, now):
    rows = payload.get("jobs")
    if not isinstance(rows,list):
        raise ValueError("Expected jobs array")
    seen = {k for j in baseline["jobs"] for k in (url_key(j.get("applyUrl")),url_key(j.get("officialSourceUrl"))) if k}
    counts = Counter()
    hosts = Counter()
    platforms = Counter()
    seen_ids = set()
    for row in rows:
        if not isinstance(row,dict):
            counts["invalid"] += 1
            continue
        rec = candidate(row, now)
        if not rec:
            counts["rejected_or_invalid"] += 1
            continue
        counts["relevant_candidate"] += 1
        key = url_key(rec.apply_url)
        if not key or not rec.apply_url_available:
            counts["invalid_url"] += 1
            continue
        if key in seen or rec.canonical_id in seen_ids:
            counts["exact_duplicate"] += 1
            continue
        seen.add(key)
        seen_ids.add(rec.canonical_id)
        counts["incremental_unverified"] += 1
        counts["official_dates_available"] += int(bool(rec.official_posted_date))
        hosts[key[0]] += 1
        platforms[rec.company] += 1
    return {"raw":len(rows),"counts":dict(counts),"candidate_hosts":dict(hosts),"candidate_platforms":dict(platforms),"limitations":["Apply URL syntax and identity key only; upstream ownership and current availability not verified","No official dates inferred from aggregator first_seen","No eligibility claims or listing publication","Attribution rendering and exact feed reuse terms still require validation"]}

def main():
    http=PoliteHTTPClient(max_retries=0)
    payload=http.get("https://aitrainer.work/data/jobs.json",timeout_sec=30,rate_limit_rps=1,use_conditional_cache=False).json()
    baseline=json.loads((ROOT/"staging/freshness_catalog.json").read_text())
    report=audit(payload,baseline,datetime.now(timezone.utc).isoformat())
    print(json.dumps(report,ensure_ascii=False),flush=True)
    Path("aitrainer-candidate-report.json").write_text(json.dumps(report,indent=2))
if __name__=="__main__":
    main()
