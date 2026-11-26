"""Bounded research of publisher index pages, NOT third-party job syndication.

No login, no JS bypass, no pagination. RemoWork is manual-only under its terms.
Only link counts and candidate outbound source domains are retained.
Robots permission is necessary but not sufficient for commercial use.
"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

AGENT = "AIJobsHubDiscoveryResearch/1.0 (+https://github.com/azarya78/ai-jobs-aggregator)"
TIMEOUT = 12
MAX_BYTES = 2_000_000
EXCLUDE_HOSTS = {
    "google.com", "www.google.com", "facebook.com", "www.facebook.com",
    "linkedin.com", "www.linkedin.com", "instagram.com", "www.instagram.com",
    "youtube.com", "www.youtube.com", "twitter.com", "x.com",
    "wordpress.org", "www.wordpress.org", "w3.org", "www.w3.org",
    "fonts.googleapis.com", "fonts.gstatic.com", "gravatar.com",
}

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for k, v in attrs:
            if k.lower() == "href" and isinstance(v, str):
                self.links.append(v)

def permitted(session, url):
    p = urlsplit(url)
    try:
        r = session.get(f"{p.scheme}://{p.netloc}/robots.txt",
                        timeout=TIMEOUT, headers={"User-Agent": AGENT})
        if r.status_code == 404:
            return True, "robots_404_no_rules"
        if r.status_code != 200:
            return False, "robots_status_" + str(r.status_code)
        rp = RobotFileParser()
        rp.parse(r.text.splitlines())
        return rp.can_fetch(AGENT, url), "robots_checked"
    except requests.RequestException as e:
        return False, "robots_error_" + type(e).__name__

def analyze_html(text, base, expected_host, internal_prefix):
    a = Links()
    a.feed(text)
    internal_urls = set()
    external = Counter()
    for raw in a.links:
        try:
            uri = urlsplit(urljoin(base, raw))
        except ValueError:
            continue
        host = (uri.hostname or "").lower().rstrip(".")
        if uri.scheme != "https" or not host or uri.username or uri.password:
            continue
        if host == expected_host:
            if uri.path.startswith(internal_prefix) and uri.path.rstrip("/") != internal_prefix.rstrip("/"):
                internal_urls.add(uri.path.rstrip("/"))
        elif host not in EXCLUDE_HOSTS and not host.endswith(".google.com"):
            # Domains only: never retain third-party tracking/referral query parameters.
            external[host] += 1
    return len(internal_urls), external

def inspect(session, source):
    row = {"id": source["id"], "name": source["name"], "url": source["url"],
           "mode": source["mode"], "state": "NOT_CHECKED", "internalCandidateLinks": 0,
           "outboundHostCount": 0, "candidateSourceDomains": [],
           "postedDatesVerified": False, "reusePermissionVerified": False}
    if source["mode"] == "manual_only":
        row["state"] = "MANUAL_REVIEW_REQUIRED"
        row["reason"] = source["reason"]
        return row
    u = urlsplit(source["url"])
    if u.scheme != "https" or u.hostname != source["expectedHost"]:
        row["state"] = "INVALID_CONFIG"
        return row
    allow, why = permitted(session, source["url"])
    row["robots"] = why
    if not allow:
        row["state"] = "ROBOTS_NOT_CLEARED"
        return row
    try:
        response = session.get(source["url"], timeout=TIMEOUT, headers={"User-Agent": AGENT})
        row["httpStatus"] = response.status_code
        final = urlsplit(response.url)
        if final.scheme != "https" or final.hostname != source["expectedHost"]:
            row["state"] = "EXTERNAL_REDIRECT"
        elif response.status_code in (401, 403, 429):
            row["state"] = "ACCESS_RESTRICTED"
        elif response.status_code != 200:
            row["state"] = "HTTP_UNAVAILABLE"
        elif "text/html" not in response.headers.get("content-type", "").lower():
            row["state"] = "NOT_HTML"
        elif len(response.content) > MAX_BYTES:
            row["state"] = "PAGE_TOO_LARGE"
        else:
            n, domains = analyze_html(response.text, response.url,
                                      source["expectedHost"], source["internalPrefix"])
            row["internalCandidateLinks"] = n
            row["outboundHostCount"] = len(domains)
            row["candidateSourceDomains"] = [{"domain": h, "links": c} for h,c in domains.most_common(25)]
            row["state"] = "INDEX_OBSERVED" if n or domains else "NO_PUBLIC_INDEX_LINKS"
    except requests.RequestException as e:
        row["state"] = "REQUEST_ERROR"
        row["errorType"] = type(e).__name__
    return row

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/third_party_discovery_research.json")
    parser.add_argument("--output", default="staging/third_party_discovery_research.json")
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text())
    assert cfg["experimentalOnly"] and cfg["doNotPublishToAndroid"]
    session = requests.Session()
    rows = [inspect(session, item) for item in cfg["sources"]]
    report = {"schemaVersion": "2.2-research-v1", "experimentalOnly": True,
              "doNotPublishToAndroid": True,
              "generatedAt": datetime.now(timezone.utc).isoformat(),
              "sources": rows}
    p = Path(args.output)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    for row in rows:
        print(row["name"], row["state"], "internal links:", row["internalCandidateLinks"],
              "outbound domains:", row["outboundHostCount"])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
