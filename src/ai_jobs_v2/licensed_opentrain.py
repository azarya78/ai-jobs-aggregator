"""Licensed OpenTrain listings, staging exploration only."""
import json
import hashlib
from collections import Counter, defaultdict
from urllib.parse import urlsplit
from .licensed_aitrainer import url_key, string
from .models import RawJobListing
from .normalizer import normalize_raw_listing, normalize_iso_date
from .freshness_metrics import parse_date, hours_since
from .validator import write_json_atomically

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


SOURCE="syndicated_opentrain"
def valid_link(link):
    if not isinstance(link,str) or any(c.isspace() or ord(c)<32 for c in link):
        return False
    try:
        p=urlsplit(link)
        return p.scheme=="https" and p.hostname=="app.opentrain.ai" and not p.username and not p.password and p.port in (None,443) and p.path not in ("","/")
    except ValueError:
        return False

def make_item(row,now):
    if row.get("status")!="OPEN":
        return None
    title,company,link,landing=(string(row.get(k)) for k in ("title","companyName","applyUrl","url"))
    sid=str(row.get("id") or "")
    if not title or not company or not sid or not valid_link(link) or not valid_link(landing):
        return None
    expiry=normalize_iso_date(row.get("validThrough"))
    if expiry and parse_date(expiry)<parse_date(now):
        return None
    desc=string(row.get("descriptionText"))
    record,_,_=normalize_raw_listing(RawJobListing(source_connector=SOURCE,platform=company,source_job_id=hashlib.sha256(sid.encode()).hexdigest()[:24],title=title,official_source_url=landing,raw_apply_url=link,description_text=desc),now)
    if record is None:
        return None
    item=record.to_android_feed_item()
    countries=row.get("countries")
    location=", ".join(v for v in countries if isinstance(v,str)) if isinstance(countries,list) else ""
    # No upstream publication claim even when a listing is native to the marketplace.
    feed_date=normalize_iso_date(row.get("datePosted"))
    if hours_since(feed_date,parse_date(now)) is None: feed_date=None
    pay=row.get("pay")
    # Preserve feed text only when supplied as text; never guess numeric currency/unit.
    pay_text=string(pay) if isinstance(pay,str) else ""
    if isinstance(pay,dict):
        pay_text=string(pay.get("displayText") or pay.get("text") or pay.get("formatted"))
    item.update(title=title,company=company,isDirect=False,date="",timestamp=0,officialPostedDate=None,
        dateSource="first_detected",marketplacePostedDate=feed_date,marketplaceDateBasis="OpenTrain reported posting",
        applyMode=string(row.get("applyMode")),location=location or "Location N/A",
        eligible="Unverified",eligibilityClass="Unverified",eligibilityConfidence="low",isIndonesian=False,
        eligibilityEvidence="Verify country eligibility on OpenTrain.",eligibilityDisclaimer="Location eligibility is unverified; check application requirements.",
        pay=(pay_text+" (feed-reported; verify)") if pay_text else "",
        compensation={"min":None,"max":None,"currency":None,"unit":None,"raw":None},
        excerpt="Via OpenTrain. Marketplace posting; verify compensation and application requirements on the source page.",
        requirementsEvidence=[],skills=[],applyUrl=link,officialSourceUrl=landing,
        sourceAttribution={"name":"OpenTrain","url":landing},
        fullDescriptionHash=hashlib.sha256(json.dumps([title,company,desc,location,pay],sort_keys=True,ensure_ascii=False).encode()).hexdigest())
    return item

def refresh_snapshot(http,path,now):
    if path.exists():
        prior=json.loads(path.read_text())
        age=hours_since(prior.get("checkedAt"),parse_date(now))
        if age is not None and age<4: return prior
    try:
        rows,pages=fetch_opentrain(http)
        buckets=defaultdict(list)
        for row in rows:
            if isinstance(row,dict) and row.get("id"): buckets[str(row["id"])].append(row)
        items=[]
        conflicts=0
        for group in buckets.values():
            if len({json.dumps(row,sort_keys=True) for row in group})!=1:
                conflicts+=len(group);continue
            item=make_item(group[0],now)
            if item: items.append(item)
        if not items: raise ValueError("No open relevant records")
        result=dict(status="SUCCESS",checkedAt=now,raw=len(rows),pages=pages,conflicts=conflicts,items=items)
    except Exception as exc:
        result=dict(status="FAILED",checkedAt=now,items=[],error=type(exc).__name__)
    write_json_atomically(path,result)
    return result

def merge_snapshot(existing,snapshot,now):
    seen={k for j in existing for v in (j.get("upstreamApplyUrl"),j.get("applyUrl"),j.get("officialSourceUrl")) if (k:=url_key(v))}
    ids={j["id"] for j in existing}
    added=[]
    for item in snapshot["items"]:
        keys={k for v in (item["applyUrl"],item["officialSourceUrl"]) if (k:=url_key(v))}
        if not keys or keys&seen or item["id"] in ids: continue
        seen.update(keys);ids.add(item["id"]);added.append(dict(item))
    ages=[hours_since(j.get("marketplacePostedDate"),parse_date(now)) for j in added]
    return added,dict(status=snapshot["status"],checkedAt=snapshot["checkedAt"],raw=snapshot.get("raw",0),pages=snapshot.get("pages",0),
        candidates=len(snapshot["items"]),added=len(added),duplicates=len(snapshot["items"])-len(added),
        marketplaceReported24h=sum(a is not None and a<=24 for a in ages),marketplaceReported7d=sum(a is not None and a<=168 for a in ages),
        officialDates=0,applyModes=dict(Counter(j["applyMode"] for j in added)))
