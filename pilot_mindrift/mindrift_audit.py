"""Isolated Mindrift timestamp and similarity audit. No feed writes."""
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from ai_jobs_v2.http_client import PoliteHTTPClient
from ai_jobs_v2.freshness_metrics import parse_date
from ai_jobs_v2.normalizer import normalize_iso_date, strip_html_to_text
from ai_jobs_v2.models import RawJobListing
from ai_jobs_v2.relevance import classify_relevance

ROOT = Path(__file__).resolve().parents[1]

def normalized(value):
    return re.sub(r"\s+", " ", str(value or "").casefold()).strip()

def timestamp(value):
    try:
        return parse_date(normalize_iso_date(value))
    except (ValueError, OverflowError, OSError, TypeError):
        return None

def audit(rows, catalog, now):
    by_code = defaultdict(list)
    for row in rows:
        if isinstance(row, dict):
            code = str(row.get("shortcode") or row.get("code") or "").strip()
            if code:
                by_code[code].append(row)
    selected = [j for j in catalog if j.get("sourceConnector") == "web_mindrift"]
    groups = defaultdict(list)
    counts = Counter()
    dates = Counter()
    samples = []
    for item in selected:
        code = item.get("sourceJobId") or item["id"].split(":", 1)[-1]
        matches = by_code.get(code, [])
        if not matches:
            counts["unmatched_catalog"] += 1
            continue
        counts["matched_catalog"] += 1
        row = matches[0]
        published = timestamp(row.get("published_on"))
        created = timestamp(row.get("created_at"))
        chosen = published or created
        recorded = timestamp(item.get("officialPostedDate"))
        if len({(str(x.get("published_on")), str(x.get("created_at"))) for x in matches}) > 1:
            counts["conflicting_dates_same_shortcode"] += 1
        counts["has_published_on"] += int(published is not None)
        counts["has_created_at"] += int(created is not None)
        counts["catalog_date_mismatch"] += int(chosen != recorded)
        if not recorded or not 0 <= (now-recorded).total_seconds() <= 7*86400:
            continue
        counts["catalog_recent_7d"] += 1
        dates[recorded.date().isoformat()] += 1
        counts["recent_created_7d"] += int(created is not None and 0 <= (now-created).total_seconds() <= 7*86400)
        counts["recent_created_older"] += int(created is not None and (now-created).total_seconds() > 7*86400)
        counts["recent_created_unknown"] += int(created is None)
        counts["recent_published_after_creation"] += int(published is not None and created is not None and published > created)
        title = normalized(item.get("title"))
        desc = normalized(strip_html_to_text(str(row.get("description") or "")))
        groups[(title, desc)].append({"id":code,"title":item.get("title"),"location":item.get("location"),"published_on":row.get("published_on"),"created_at":row.get("created_at")})
        if len(samples)<15:
            samples.append(groups[(title, desc)][-1])
    clusters = sorted((v for v in groups.values() if len(v)>1), key=len, reverse=True)
    return {"raw_records":len(rows),"unique_shortcodes":len(by_code),"catalog_jobs":len(selected),
        "counts":dict(counts),"recent_date_histogram":dict(sorted(dates.items())),
        "recent_title_description_groups":len(groups),"multi_shortcode_groups":len(clusters),
        "largest_groups":[{"size":len(v),"examples":v[:5]} for v in clusters[:10]],
        "samples":samples,
        "limitations":["Same title and description is a similarity signal, not proof of identical projects.",
        "Creation date is not necessarily first publication.", "Live widget and saved catalog can differ.",
        "Audit covers catalog representatives, not every upstream record."]}

def main():
    cfg = json.loads((ROOT/"config/sources.json").read_text())
    source = next(x for x in cfg["sources"] if x["id"]=="web_mindrift")
    data = PoliteHTTPClient(max_retries=0).get(source["api_endpoint"],timeout_sec=25,rate_limit_rps=1).json()
    if not isinstance(data,dict) or not isinstance(data.get("jobs"),list):
        raise ValueError("Invalid Workable jobs payload")
    catalog = json.loads((ROOT/"staging/freshness_catalog.json").read_text())["jobs"]
    report = audit(data["jobs"],catalog,datetime.now(timezone.utc))
    print(json.dumps(report,ensure_ascii=False))
    Path("mindrift-audit-report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))

if __name__ == "__main__":
    main()
