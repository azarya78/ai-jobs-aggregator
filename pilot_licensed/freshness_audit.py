"""Audit licensed OpenTrain and SOJI freshness without publishing."""
import json
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
from ai_jobs_v2.http_client import PoliteHTTPClient
from ai_jobs_v2.freshness_metrics import parse_date,hours_since
from ai_jobs_v2.licensed_aitrainer import url_key
from ai_jobs_v2.models import RawJobListing
from ai_jobs_v2.normalizer import normalize_raw_listing
ROOT=Path(__file__).resolve().parents[1]
def fetch_opentrain(http,max_pages=100):
    rows=[]
    cursor=None
    visited=set()
    for _ in range(max_pages):
        params={"limit":50,"order":"newest","sub":"ai-jobs-hub"}
        if cursor: params["cursor"]=cursor
        payload=http.get("https://app.opentrain.ai/api/public/v1/jobs",params=params,timeout_sec=30,rate_limit_rps=1,use_conditional_cache=False).json()
        page=payload.get("jobs")
        if not isinstance(page,list): raise ValueError("Missing jobs")
        rows.extend(page)
        cursor=payload.get("nextCursor")
        if not cursor: return rows,len(visited)+1
        if not isinstance(cursor,str) or cursor in visited or not page: raise ValueError("Invalid cursor")
        visited.add(cursor)
    raise ValueError("Pagination limit reached")

def audit(rows,baseline,now,source):
    seen={k for j in baseline for v in (j.get("upstreamApplyUrl"),j.get("applyUrl"),j.get("officialSourceUrl")) if (k:=url_key(v))}
    counts=Counter()
    companies=Counter()
    hosts=Counter()
    ids=set()
    for row in rows:
        if not isinstance(row,dict): counts["invalid"]+=1;continue
        sid=str(row.get("id") or "")
        title=row.get("title") or ""
        company=(row.get("companyName") if source=="OpenTrain" else row.get("platform")) or ""
        link=(row.get("applyUrl") if source=="OpenTrain" else row.get("platformUrl")) or ""
        desc=(row.get("descriptionText") if source=="OpenTrain" else row.get("summary")) or ""
        if not all(isinstance(v,str) and v.strip() for v in (sid,title,company,link)) or not url_key(link):
            counts["invalid"]+=1;continue
        if sid in ids: counts["duplicate_id"]+=1;continue
        ids.add(sid)
        rec,_,_=normalize_raw_listing(RawJobListing(source_connector="audit",platform=company,source_job_id=sid,title=title,official_source_url=link,raw_apply_url=link,description_text=desc if isinstance(desc,str) else ""),now)
        if rec is None: counts["irrelevant"]+=1;continue
        counts["relevant"]+=1
        key=url_key(link)
        if key in seen: counts["duplicate_url"]+=1;continue
        seen.add(key)
        counts["incremental"]+=1
        companies[company]+=1
        hosts[key[0]]+=1
        date=row.get("datePosted" if source=="OpenTrain" else "postedAt")
        age=hours_since(date,parse_date(now))
        if age is None: counts["unknown_date"]+=1
        else:
            counts["feed_date_24h"]+=int(age<=24)
            counts["feed_date_7d"]+=int(age<=168)
        if source=="OpenTrain":
            counts["status_"+str(row.get("status"))]+=1
            counts["apply_mode_"+str(row.get("applyMode"))]+=1
    return dict(source=source,raw=len(rows),counts=dict(counts),companies=dict(companies),hosts=dict(hosts),dateBasis="OpenTrain reported marketplace posting" if source=="OpenTrain" else "SOJI first listed",official_dates_verified=0)
def main():
    http=PoliteHTTPClient(max_retries=0)
    now=datetime.now(timezone.utc).isoformat()
    baseline=json.loads((ROOT/"staging/freshness_catalog.json").read_text())["jobs"]
    report=[]
    for name in ("OpenTrain","SOJI"):
        try:
            if name=="OpenTrain": rows,pages=fetch_opentrain(http)
            else:
                rows=http.get("https://hiring.sojistudio.com/feed/jobs.json",timeout_sec=30,rate_limit_rps=1,use_conditional_cache=False).json()["items"]
                pages=1
            entry=audit(rows,baseline,now,name)
            entry.update(status="SUCCESS",pages=pages)
        except Exception as exc: entry=dict(source=name,status="FAILED",error=str(exc)[:300])
        report.append(entry)
        print(json.dumps(entry,ensure_ascii=False),flush=True)
    Path("licensed-freshness-report.json").write_text(json.dumps(report,indent=2))
    if any(r["status"]!="SUCCESS" for r in report): raise ValueError("Feed audit failed")
if __name__=="__main__": main()
