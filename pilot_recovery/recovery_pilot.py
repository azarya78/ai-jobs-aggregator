"""Bounded, read-only recovery pilot; no login or bypass; bounded same-origin robots redirect."""
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup
from audit_new_sources import USER_AGENT

SOURCES = [
    ("TELUS Global", "https://jobs.telusdigital.com/search/cfm5/ai-community/jobs", r"^/jobs/\d+-[^/]+/?$"),
    ("Outlier", "https://app.outlier.ai/opportunities?hl=en-US", r"^/opportunities/\d+/?$"),
    ("Micro1 public index", "https://www.micro1.ai/experts/opportunities", r"^/experts/opportunities/[^/]+/?$"),
    ("Begun public index", "https://app.begun.com/opportunities", r"^/opportunities/[^/]+/?$"),
]

def extract(html, url, pattern):
    soup = BeautifulSoup(html, "html.parser")
    found = set()
    for a in soup.find_all("a", href=True):
        target = urlsplit(urljoin(url, a["href"]))
        if target.scheme == "https" and target.netloc == urlsplit(url).netloc and re.fullmatch(pattern, target.path):
            found.add(target._replace(query="", fragment="").geturl().rstrip("/"))
    structured = 0
    def visit(value):
        nonlocal structured
        if isinstance(value, dict):
            types = value.get("@type", [])
            if types == "JobPosting" or isinstance(types, list) and "JobPosting" in types:
                structured += 1
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            visit(json.loads(node.get_text()))
        except (ValueError, TypeError):
            continue
    return {"matchingLinks":len(found), "sampleLinks":sorted(found)[:5], "structuredJobPostingCount":structured}

def inspect(session, name, url, pattern):
    out = dict(platform=name,url=url,status="NOT_TESTED",syndicationApproved=False,postingDatesVerified=False)
    try:
        p=urlsplit(url)
        robots=session.get(p.scheme+"://"+p.netloc+"/robots.txt", timeout=15, allow_redirects=False)
        out["robotsInitialStatus"]=robots.status_code
        if robots.status_code in (301,302,307,308):
            dest=urlsplit(urljoin(p.scheme+"://"+p.netloc+"/robots.txt", robots.headers.get("Location","")))
            if dest.scheme=="https" and dest.netloc==p.netloc and dest.path in ("/robots.txt","/robots.txt/") and not dest.query and not dest.fragment:
                import time
                time.sleep(2)
                robots=session.get(dest.geturl(),timeout=15,allow_redirects=False)
        out["robotsStatus"]=robots.status_code
        if robots.status_code==200:
            rp=RobotFileParser()
            rp.parse(robots.text.splitlines())
            if not rp.can_fetch(USER_AGENT,url):
                out["status"]="ROBOTS_DISALLOWED"
                return out
        elif robots.status_code!=404:
            out["status"]="ROBOTS_NOT_CLEARED"
            return out
        import time
        time.sleep(2)
        response=session.get(url,timeout=20,allow_redirects=False)
        out["httpStatus"]=response.status_code
        if response.status_code!=200:
            out["status"]="ACCESS_RESTRICTED" if response.status_code in (401,403,429,451) else "HTTP_UNAVAILABLE"
            return out
        if "text/html" not in response.headers.get("Content-Type","").lower():
            out["status"]="UNSUPPORTED_CONTENT_TYPE"
            return out
        out.update(extract(response.text,url,pattern))
        out["status"]="PUBLIC_INDEX_CANDIDATE" if out["matchingLinks"] or out["structuredJobPostingCount"] else "NO_STATIC_JOB_LINKS"
    except requests.RequestException as e:
        out.update(status="REQUEST_ERROR",errorType=type(e).__name__)
    return out

def main():
    with requests.Session() as session:
        session.headers.update({"User-Agent":USER_AGENT})
        reports=[inspect(session,*s) for s in SOURCES]
    output={"experimentalOnly":True,"doNotPublishToAndroid":True,"sources":reports}
    print(json.dumps(output))
    Path("recovery-report.json").write_text(json.dumps(output,indent=2))

if __name__=="__main__":
    main()
