"""Bounded live distribution API audit. No catalog writes."""
import json
from collections import Counter
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
    return dict(rows=len(rows),pages=pages,fields=dict(fields),platforms=dict(platforms),attribution_hosts=dict(hosts),invalid_attribution_links=invalid,repeated_landing_paths=repeated,official_dates_inferred=0,experimentalOnly=True,doNotPublishToAndroid=True)

def main():
    rows,pages=fetch_pages(PoliteHTTPClient(max_retries=0))
    report=summarize(rows,pages)
    print(json.dumps(report,ensure_ascii=False),flush=True)
    Path("aitrainer-live-report.json").write_text(json.dumps(report,indent=2))
    if report["invalid_attribution_links"] or report["repeated_landing_paths"]:
        raise ValueError("Attribution or identity gate failed")
if __name__=="__main__":
    main()
