"""Bounded first-party source-access audit. Not a job scraper or public feed.

Checks robots.txt then fetches one publicly linked landing/index page per source.
Reports only observed matching link counts, host response, and limitations.
Never requests login/API endpoints or invents posting dates.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = "AIJobsHubSourceAudit/1.0 (+https://github.com/azarya78/ai-jobs-aggregator)"
TIMEOUT = 12


class AnchorCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        for k, v in attrs:
            if k == "href" and isinstance(v, str):
                self.hrefs.append(v)


def count_opportunity_links(html, base_url, expected_host, path_prefix):
    parser = AnchorCollector()
    parser.feed(html)
    urls = set()
    for href in parser.hrefs:
        url = urljoin(base_url, href).split("#", 1)[0].split("?", 1)[0]
        p = urlsplit(url)
        if p.scheme == "https" and (p.hostname or "").lower() == expected_host.lower():
            if p.path.rstrip("/").startswith(path_prefix.rstrip("/") + "/"):
                urls.add(url.rstrip("/"))
    return sorted(urls)


def robots_permission(session, url):
    p = urlsplit(url)
    robots_url = f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        response = session.get(robots_url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        if response.status_code == 404:
            return True, "robots_404_no_rules"
        if response.status_code != 200:
            return False, f"robots_http_{response.status_code}"
        rp = RobotFileParser()
        rp.parse(response.text.splitlines())
        return rp.can_fetch(USER_AGENT, url), "robots_checked"
    except requests.RequestException as exc:
        return False, f"robots_unavailable:{type(exc).__name__}"


def inspect_source(session, source):
    url = source["url"]
    expected_host = source["host"]
    output = {"id": source["id"], "platform": source["platform"],
              "url": url, "status": "NOT_TESTED", "matchingLinkCount": 0,
              "sampleOfficialLinks": [], "sourceType": "official_public_page",
              "postingDatesVerified": False, "syndicationApproved": False}
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != expected_host:
        output["status"] = "INVALID_SOURCE_CONFIG"
        return output
    allowed, reason = robots_permission(session, url)
    output["robotsCheck"] = reason
    if not allowed:
        output["status"] = "ROBOTS_NOT_CLEARED"
        return output
    try:
        response = session.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        output["httpStatus"] = response.status_code
        final = urlsplit(response.url)
        if final.scheme != "https" or final.hostname != expected_host:
            output["status"] = "REDIRECT_OUTSIDE_CONFIGURED_HOST"
        elif response.status_code in (401, 403, 429):
            output["status"] = "ACCESS_RESTRICTED"
        elif response.status_code != 200:
            output["status"] = "HTTP_UNAVAILABLE"
        elif "text/html" not in response.headers.get("Content-Type", "").lower():
            output["status"] = "UNSUPPORTED_CONTENT_TYPE"
        else:
            urls = count_opportunity_links(response.text, response.url, expected_host, source["pathPrefix"])
            output["matchingLinkCount"] = len(urls)
            output["sampleOfficialLinks"] = urls[:3]
            output["status"] = "PUBLIC_LIST_LINKS" if urls else "NO_STATIC_JOB_LINKS"
    except requests.RequestException as exc:
        output["status"] = "REQUEST_ERROR"
        output["errorType"] = type(exc).__name__
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/new_source_radar.json")
    parser.add_argument("--output", default="staging/new_source_radar_report.json")
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    assert cfg["experimentalOnly"] is True and cfg["doNotPublishToAndroid"] is True
    session = requests.Session()
    reports = [inspect_source(session, source) for source in cfg["sources"]]
    report = {"schemaVersion": "source-radar-v1", "experimentalOnly": True,
              "doNotPublishToAndroid": True,
              "checkedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "sourcesChecked": len(reports),
              "sourcesWithPublicIndexLinks": sum(r["status"] == "PUBLIC_LIST_LINKS" for r in reports),
              "sources": reports}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
