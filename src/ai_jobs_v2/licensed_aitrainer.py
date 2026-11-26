"""Licensed AITrainer discovery, staging catalog only. CC BY 4.0."""
import json
import hashlib
from collections import Counter, defaultdict
from urllib.parse import urlsplit, parse_qsl
from .http_client import PoliteHTTPClient
from .models import RawJobListing
from .normalizer import normalize_raw_listing, strip_html_to_text
from .freshness_metrics import parse_date, hours_since
from .url_sanitizer import sanitize_official_apply_url
from .validator import write_json_atomically

ENDPOINT = "https://aitrainer.work/api/jobs.json"
def fetch_pages(http, max_pages=20):
    offset = 0
    visited = set()
    rows = []
    expected = None
    for _ in range(max_pages):
        if offset in visited:
            raise ValueError("Repeated page offset")
        visited.add(offset)
        payload = http.get(ENDPOINT, params={"limit":500,"offset":offset,"source":"ai-jobs-hub"}, timeout_sec=30, rate_limit_rps=1, use_conditional_cache=False).json()
        page = payload.get("jobs")
        if not isinstance(page,list):
            raise ValueError("Expected jobs array; fields=" + str(list(payload)))
        total = payload.get("total")
        if not isinstance(total,int) or total < 0:
            raise ValueError("Missing total")
        if expected is None:
            expected = total
        elif total != expected:
            raise ValueError("Feed total changed during pagination")
        rows.extend(page)
        nxt = payload.get("next_offset")
        if nxt is None:
            if len(rows) != expected:
                raise ValueError("Incomplete feed")
            return rows, len(visited)
        if not isinstance(nxt,int) or nxt <= offset or not page:
            raise ValueError("Invalid next offset")
        offset = nxt
    raise ValueError("Page limit reached; incomplete feed")

def partition_landing_rows(rows):
    """Keep only unambiguous landing identities; never choose among conflicting rows."""
    groups = defaultdict(list)
    invalid = 0
    for row in rows:
        if not isinstance(row, dict):
            invalid += 1
            continue
        url = row.get("url")
        try:
            p = urlsplit(url) if isinstance(url,str) else urlsplit("")
            valid = (p.scheme == "https" and p.hostname in ("aitrainer.work","www.aitrainer.work")
                     and p.path not in ("","/") and not p.username and not p.password
                     and p.port in (None,443))
        except ValueError:
            valid = False
        if not valid:
            invalid += 1
            continue
        groups[p.path.rstrip("/")].append(row)
    safe = []
    exact = 0
    conflicts = 0
    conflict_groups = 0
    conflict_platforms = Counter()
    for group in groups.values():
        fingerprints = {json.dumps(row,sort_keys=True,ensure_ascii=False) for row in group}
        if len(fingerprints) == 1:
            safe.append(group[0])
            exact += len(group)-1
        else:
            conflicts += len(group)
            conflict_groups += 1
            conflict_platforms.update(str(row.get("company") or row.get("platform") or "unknown") for row in group)
    assert len(safe)+exact+conflicts+invalid == len(rows)
    return safe, dict(safe_rows=len(safe), exact_duplicate_rows=exact,
                      quarantined_conflict_rows=conflicts, conflict_groups=conflict_groups,
                      conflict_platforms=dict(conflict_platforms), invalid_rows=invalid)


SOURCE = "syndicated_aitrainer"
CREDIT = "Via AITrainer.work (CC BY 4.0). Verify details on the source page."
def string(value):
    return value.strip() if isinstance(value,str) else ""
def url_key(value):
    clean, ok, _ = sanitize_official_apply_url(string(value))
    if not ok:
        return None
    p = urlsplit(clean)
    return (p.hostname, p.path.rstrip("/"), tuple(sorted(parse_qsl(p.query))))
def make_item(row, now):
    title, company, url = (string(row.get(k)) for k in ("title","company","url"))
    upstream = string(row.get("apply_url"))
    if not (title and company and url and url_key(upstream)):
        return None
    key = urlsplit(url).path.rstrip("/")
    sid = hashlib.sha256(key.encode()).hexdigest()[:24]
    description = string(row.get("description"))
    record,_,_ = normalize_raw_listing(RawJobListing(source_connector=SOURCE,platform=company,
        source_job_id=sid,title=title,official_source_url=url,raw_apply_url=url,
        description_text=description,official_posted_at=None),now)
    if not record or not record.apply_url_available:
        return None
    item = record.to_android_feed_item()
    # Existing APK treats legacy date on non-direct records as publication.
    # Never expose a discovery timestamp in that field.
    pay = string(row.get("pay_text"))
    source = string(row.get("pay_source"))
    location = row.get("geographic_restrictions")
    if isinstance(location,list):
        location = ", ".join(string(v) for v in location if string(v))
    location = string(location)
    # Do not infer global eligibility from aggregator descriptions.
    item.update(title=title,company=company,isDirect=False,officialPostedDate=None,
        date="",timestamp=0,dateSource="first_detected",
        location=location or "Location N/A", eligible="Unverified",eligibilityClass="Unverified",
        eligibilityConfidence="low",eligibilityEvidence="Third-party listing; verify on the source page.",
        isIndonesian=False,eligibilityDisclaimer="Location eligibility is unverified; check official requirements.",
        pay=(pay+" (feed-reported; verify)") if pay else "",
        compensation={"min":None,"max":None,"currency":None,"unit":None,"raw":None},
        excerpt=CREDIT, requirementsEvidence=[], skills=[],
        fullDescriptionHash=hashlib.sha256(json.dumps([title,company,location,pay,description],ensure_ascii=False).encode()).hexdigest(),
        sourceAttribution={"name":"AITrainer.work","url":url,"license":"https://creativecommons.org/licenses/by/4.0/"},
        upstreamApplyUrl=upstream, aggregatorFirstSeen=string(row.get("first_seen")) or None,
        aggregatorPaySource=source or None, officialSourceUrl=url,applyUrl=url)
    return item

def refresh_snapshot(http, path, now):
    if path.exists():
        prior=json.loads(path.read_text(encoding="utf-8"))
        age=hours_since(prior.get("checkedAt"),parse_date(now))
        if age is not None and age<12:
            return prior
    try:
        rows,pages=fetch_pages(http)
        safe,identity=partition_landing_rows(rows)
        if identity["invalid_rows"] or not safe:
            raise ValueError("Licensed feed identity gate failed")
        items=[item for row in safe if (item:=make_item(row,now)) is not None]
        if not items:
            raise ValueError("No relevant licensed candidates")
        snapshot=dict(checkedAt=now,status="SUCCESS",items=items,raw=len(rows),pages=pages,identity=identity)
    except Exception as exc:
        # Fail closed: do not show stale syndicated listings after a failed refresh.
        snapshot=dict(checkedAt=now,status="FAILED",items=[],error=type(exc).__name__)
    write_json_atomically(path,snapshot)
    return snapshot

def merge_snapshot(direct_items,snapshot):
    seen={k for item in direct_items for value in (item.get("applyUrl"),item.get("officialSourceUrl")) if (k:=url_key(value))}
    ids={item["id"] for item in direct_items}
    added=[]
    duplicates=0
    for item in snapshot["items"]:
        key=url_key(item.get("upstreamApplyUrl"))
        if not key or key in seen or item["id"] in ids:
            duplicates+=1
            continue
        seen.add(key)
        ids.add(item["id"])
        added.append(dict(item))
    return added,dict(status=snapshot["status"],checkedAt=snapshot["checkedAt"],raw=snapshot.get("raw",0),
        identity=snapshot.get("identity",{}),relevantCandidates=len(snapshot["items"]),
        crossSourceOrInternalDuplicates=duplicates,added=len(added),
        platforms=dict(Counter(item["company"] for item in added)),officialDates=0,
        limitations=["Feed reported open status; upstream availability not independently verified.",
        "Exact URL deduplication only; all syndicated eligibility unverified.",
        "No official posting dates inferred."])
