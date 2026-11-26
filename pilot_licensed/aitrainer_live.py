"""Bounded live distribution API audit. No catalog writes."""
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit
from ai_jobs_v2.http_client import PoliteHTTPClient

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

def summarize(rows,pages):
    fields=Counter()
    platforms=Counter()
    hosts=Counter()
    ids=set()
    invalid=0
    repeated=0
    for row in rows:
        if not isinstance(row,dict):
            invalid+=1
            continue
        fields.update(k for k,v in row.items() if v is not None and v!="")
        platform=row.get("company") or row.get("platform")
        if isinstance(platform,str): platforms[platform]+=1
        url=row.get("url")
        p=urlsplit(url) if isinstance(url,str) else urlsplit("")
        if p.scheme=="https" and p.hostname in ("aitrainer.work","www.aitrainer.work") and p.path not in ("","/"):
            hosts[p.hostname]+=1
            key=p.path.rstrip("/")
            if key in ids: repeated+=1
            ids.add(key)
        else: invalid+=1
    _, identity = partition_landing_rows(rows)
    return dict(identity=identity, rows=len(rows),pages=pages,fields=dict(fields),platforms=dict(platforms),attribution_hosts=dict(hosts),invalid_attribution_links=invalid,repeated_landing_paths=repeated,official_dates_inferred=0,experimentalOnly=True,doNotPublishToAndroid=True)

def main():
    rows,pages=fetch_pages(PoliteHTTPClient(max_retries=0))
    report=summarize(rows,pages)
    print(json.dumps(report,ensure_ascii=False),flush=True)
    Path("aitrainer-live-report.json").write_text(json.dumps(report,indent=2))
    if report["identity"]["invalid_rows"] or not report["identity"]["safe_rows"]:
        raise ValueError("Attribution or empty-safe-feed gate failed")
if __name__=="__main__":
    main()
