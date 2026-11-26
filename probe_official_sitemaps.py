"""Read-only official sitemap discovery feasibility probe.

No login, no auth bypass, no indexing of aggregator data.
Checks robots.txt, probes up to 3 official XML sitemap files per host,
and reports counts plus a few canonical URLs, never publishing to Android.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree as ET
import json
import requests

AGENT = "AIJobsHubSourceRadar/1.0 (+https://github.com/azarya78/ai-jobs-aggregator)"
MAX_BYTES = 3_000_000

def permit(session, url, timeout=12):
    u = urlsplit(url)
    try:
        r = session.get(f"{u.scheme}://{u.netloc}/robots.txt", headers={"User-Agent": AGENT}, timeout=timeout)
        if r.status_code == 404:
            return True, "robots_missing"
        if r.status_code != 200:
            return False, "robots_http_" + str(r.status_code)
        parser=RobotFileParser()
        parser.parse(r.text.splitlines())
        return parser.can_fetch(AGENT,url), "robots_checked"
    except requests.RequestException as exc:
        return False, type(exc).__name__

def locs_from_xml(text):
    root=ET.fromstring(text)
    kind=root.tag.rsplit("}",1)[-1].lower()
    if kind not in ("urlset","sitemapindex"):
        return kind, []
    out=[]
    for child in root:
        if child.tag.rsplit("}",1)[-1] not in ("url","sitemap"):
            continue
        for n in child:
            if n.tag.rsplit("}",1)[-1] == "loc" and n.text:
                out.append(n.text.strip())
    return kind,out

def inspect(session, source, limits):
    out={"id":source["id"],"platform":source["platform"],"state":"NOT_CHECKED",
         "sitemapsFetched":0,"officialJobUrlCount":0,"sampleOfficialUrls":[],"httpResults":[],
         "publishedDatesVerified":False,"reusePermissionVerified":False}
    root=urlsplit(source["root"]); first=urlsplit(source["sitemap"])
    if root.scheme!="https" or first.scheme!="https" or root.hostname!=first.hostname:
        out["state"]="INVALID_CONFIG";return out
    allowed,reason=permit(session,source["sitemap"],limits["timeoutSeconds"])
    out["robots"]=reason
    if not allowed:
        out["state"]="ROBOTS_NOT_CLEARED";return out
    queue=[source["sitemap"]]; seen=set(); jobs=set(); code=[]
    while queue and len(seen)<limits["maxSitemapDocuments"]:
        url=queue.pop(0)
        if url in seen: continue
        seen.add(url)
        allowed,reason=permit(session,url,limits["timeoutSeconds"])
        if not allowed:
            code.append("robots_blocked_nested"); continue
        try:
            r=session.get(url,headers={"User-Agent":AGENT},timeout=limits["timeoutSeconds"])
            code.append(r.status_code)
            if r.status_code!=200: continue
            final=urlsplit(r.url)
            if final.scheme!="https" or final.hostname!=root.hostname: continue
            if len(r.content)>MAX_BYTES:continue
            kind,locs=locs_from_xml(r.text)
            out["sitemapsFetched"]+=1
            for loc in locs[:limits["maxUrlsPerDocument"]]:
                p=urlsplit(loc)
                if p.scheme!="https" or p.hostname!=root.hostname: continue
                if kind=="sitemapindex" and p.path.endswith(".xml") and loc not in seen and loc not in queue:
                    queue.append(loc)
                elif kind=="urlset" and p.path.startswith(source["jobPathPrefix"]) and p.path.rstrip("/")!=source["jobPathPrefix"].rstrip("/"):
                    jobs.add(f"{p.scheme}://{p.netloc}{p.path.rstrip('/')}")
        except (requests.RequestException, ET.ParseError, ValueError) as exc:
            code.append(type(exc).__name__)
    out["httpResults"]=code
    out["officialJobUrlCount"]=len(jobs)
    out["sampleOfficialUrls"]=sorted(jobs)[:5]
    out["state"]="OFFICIAL_JOB_URLS" if jobs else ("SITEMAP_FETCHED_NO_JOBS" if out["sitemapsFetched"] else "SITEMAP_UNAVAILABLE")
    return out

def main():
    conf=json.loads(Path("config/official_sitemap_probe.json").read_text())
    assert conf["experimentalOnly"] and conf["doNotPublishToAndroid"]
    session=requests.Session()
    rows=[inspect(session,x,conf["limits"]) for x in conf["sources"]]
    out={"schemaVersion":"official-sitemap-probe-v1","experimentalOnly":True,
         "doNotPublishToAndroid":True,
         "checkedAt":datetime.now(timezone.utc).isoformat(),"sources":rows}
    p=Path("staging/official_sitemap_probe.json")
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(out,indent=2)+"\n")
    for r in rows:print(r["platform"],r["state"],r["officialJobUrlCount"],r["httpResults"])
if __name__=="__main__":main()
