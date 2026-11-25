"""Public Career Page & JSON-LD / Next.js SSR Extractors (Mercor, Micro1, Alignerr, OneForma, TELUS AI, Mindrift, iMerit)."""
from __future__ import annotations
import json
import re
from typing import List, Dict, Any
from urllib.parse import urljoin
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore

from .base import BaseConnector
from ..models import RawJobListing


def extract_json_ld_job_postings(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    postings: List[Dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw_txt = (script.string or script.get_text() or "").strip()
        if not raw_txt:
            continue
        try:
            data = json.loads(raw_txt)
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        for node in stack:
            if not isinstance(node, dict):
                continue
            if node.get("@type") == "JobPosting":
                postings.append(node)
            elif isinstance(node.get("@graph"), list):
                for sub in node["@graph"]:
                    if isinstance(sub, dict) and sub.get("@type") == "JobPosting":
                        postings.append(sub)
    return postings


class MercorConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        target_url = self.config.get("html_endpoint") or self.official_url
        resp = self.http.get(target_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp.text, "html.parser")
        listings: List[RawJobListing] = []

        next_tag = soup.find("script", id="__NEXT_DATA__")
        if next_tag and next_tag.string:
            try:
                nd = json.loads(next_tag.string)
                items = (((nd.get("props") or {}).get("pageProps") or {}).get("jobs") or [])
                for jb in items:
                    if not isinstance(jb, dict):
                        continue
                    jid = str(jb.get("id") or jb.get("listingId") or "").strip()
                    title = str(jb.get("title") or "").strip()
                    if not jid or not title:
                        continue
                    slug = str(jb.get("slug") or "")
                    url = str(jb.get("url") or f"https://work.mercor.com/jobs/{jid}/{slug}".rstrip("/"))
                    closed = bool(jb.get("isClosed")) or "no longer accepting applications" in str(jb.get("statusText", "")).lower()
                    listings.append(
                        RawJobListing(
                            source_connector=self.source_id,
                            platform=self.platform,
                            source_job_id=jid,
                            title=title,
                            official_source_url=url,
                            raw_apply_url=url,
                            location_raw=str(jb.get("location") or "Remote"),
                            description_text=str(jb.get("description") or title),
                            compensation_raw=str(jb.get("rate") or jb.get("pay") or ""),
                            official_posted_at=str(jb["createdAt"]) if jb.get("createdAt") else None,
                            logo_url=self.logo,
                            pure_ai_eval_board=self.pure_ai_eval_board,
                            is_explicitly_closed=closed,
                        )
                    )
                if listings:
                    return listings
            except Exception:
                pass

        seen = set()
        for a in soup.select("a[href*='/jobs/']"):
            href = (a.get("href") or "").strip()
            full_url = urljoin("https://work.mercor.com", href)
            m = re.search(r"/jobs/(list_[A-Za-z0-9_\-]+)", full_url)
            jid = m.group(1) if m else full_url
            if jid in seen:
                continue
            seen.add(jid)

            container = a if len(a.get_text(strip=True)) > 20 else (a.find_parent(["li", "article", "div"]) or a)
            card_text = container.get_text(" ", strip=True)
            title_el = container.find(["h2", "h3", "h4", "strong"])
            title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
            if not title or title.lower() in ("apply", "view all opportunities"):
                continue

            closed = "no longer accepting applications" in card_text.lower()
            m_pay = re.search(r"\$\d+(?:\s*-\s*\$\d+|\s*-\s*\d+)?\s*/\s*(?:hour|hr|task)", card_text, re.IGNORECASE)
            comp_raw = m_pay.group(0) if m_pay else ""

            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=title,
                    official_source_url=full_url,
                    raw_apply_url=full_url,
                    location_raw="Remote",
                    description_text=card_text,
                    compensation_raw=comp_raw,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                    is_explicitly_closed=closed,
                )
            )
        return listings


class Micro1Connector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        resp = self.http.get(self.api_endpoint, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp.text, "html.parser")
        listings: List[RawJobListing] = []

        for ld in extract_json_ld_job_postings(soup):
            jid = str(ld.get("identifier", {}).get("value") if isinstance(ld.get("identifier"), dict) else ld.get("url") or "")
            url = str(ld.get("url") or self.official_url)
            m = re.search(r"/post/([0-9a-f\-]{16,})", url)
            if m:
                jid = m.group(1)
            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=str(ld.get("title") or ""),
                    official_source_url=url,
                    raw_apply_url=url,
                    location_raw="Remote",
                    description_text=str(ld.get("description") or ""),
                    official_posted_at=str(ld.get("datePosted")) if ld.get("datePosted") else None,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        if listings:
            return listings

        seen = set()
        for card in soup.select("[data-job-id], a[href*='jobs.micro1.ai/post/'], a[href*='/post/']"):
            href = (card.get("href") or "").strip()
            full_url = urljoin("https://jobs.micro1.ai", href) if href else self.official_url
            m = re.search(r"/post/([0-9a-fA-F\-]{8,})", full_url)
            jid = card.get("data-job-id") or (m.group(1) if m else "")
            container = card if card.name in ("article", "div", "li") else (card.find_parent(["article", "li", "div"]) or card)
            text = container.get_text(" ", strip=True)
            title_el = container.find(["h2", "h3", "h4"])
            title = title_el.get_text(" ", strip=True) if title_el else ""
            if not title:
                continue
            key = jid or title
            if key in seen:
                continue
            seen.add(key)

            m_pay = re.search(r"Pay\s*:\s*([^\n|]+?/(?:h|hr|hour))|\$\d+\s*-\s*\$?\d+\s*/\s*(?:h|hr|hour)", text, re.IGNORECASE)
            comp_raw = (m_pay.group(1) or m_pay.group(0)).strip() if m_pay else ""
            m_date = re.search(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}\b", text)

            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=str(jid),
                    title=title,
                    official_source_url=full_url,
                    raw_apply_url=full_url,
                    location_raw="Remote",
                    description_text=f"{text} Train next generation AI models.",
                    compensation_raw=comp_raw,
                    official_posted_at=m_date.group(0) if m_date else None,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        return listings


class AlignerrConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        resp = self.http.get(self.api_endpoint, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp.text, "html.parser")
        listings: List[RawJobListing] = []
        seen = set()

        for a in soup.select("a[href*='/jobs/']"):
            href = (a.get("href") or "").strip()
            full_url = urljoin("https://www.alignerr.com", href)
            m = re.search(r"/jobs/([0-9a-zA-Z\-]{6,})", full_url)
            if not m:
                continue
            jid = m.group(1)
            if jid in seen:
                continue
            seen.add(jid)

            container = a if len(a.get_text(strip=True)) > 25 else (a.find_parent(["div", "li", "article"]) or a)
            text = container.get_text(" ", strip=True)
            title_el = container.find(["h2", "h3", "h4"])
            title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
            if not title or title.lower() == "apply":
                continue

            m_pay = re.search(r"\$\d+(?:\s*-\s*\$?\d+)?\s*/\s*(?:hr|hour)", text, re.IGNORECASE)
            comp_raw = m_pay.group(0) if m_pay else ""
            loc_el = container.find(attrs={"data-location": True})
            loc_str = loc_el["data-location"] if loc_el else "Remote"

            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=title,
                    official_source_url=full_url,
                    raw_apply_url=full_url,
                    location_raw=loc_str,
                    description_text=text,
                    compensation_raw=comp_raw,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        return listings


class OneFormaConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        try:
            resp_api = self.http.get(self.api_endpoint, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
            data = resp_api.json()
            if isinstance(data, list):
                out: List[RawJobListing] = []
                for wp_item in data:
                    if not isinstance(wp_item, dict):
                        continue
                    jid = str(wp_item.get("id") or "").strip()
                    t_obj = wp_item.get("title")
                    title = str(t_obj.get("rendered") if isinstance(t_obj, dict) else t_obj or "").strip()
                    link = str(wp_item.get("link") or self.official_url).strip()
                    c_obj = wp_item.get("content") or wp_item.get("excerpt") or {}
                    desc = str(c_obj.get("rendered") if isinstance(c_obj, dict) else c_obj or "")
                    if jid and title:
                        out.append(
                            RawJobListing(
                                source_connector=self.source_id,
                                platform=self.platform,
                                source_job_id=jid,
                                title=title,
                                official_source_url=link,
                                raw_apply_url=link,
                                location_raw=str(wp_item.get("location") or ""),
                                description_text=desc,
                                official_posted_at=str(wp_item["date_gmt"]) + "Z" if wp_item.get("date_gmt") else None,
                                logo_url=self.logo,
                                pure_ai_eval_board=self.pure_ai_eval_board,
                            )
                        )
                return out
        except Exception:
            pass

        html_url = self.config.get("html_endpoint") or self.official_url
        resp_html = self.http.get(html_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp_html.text, "html.parser")
        listings: List[RawJobListing] = []
        seen = set()

        for a in soup.select("a[href*='/job/'], a[href*='/jobs/']"):
            href = (a.get("href") or "").strip()
            full_url = urljoin("https://www.oneforma.com", href)
            if full_url.rstrip("/") == html_url.rstrip("/"):
                continue
            slug = full_url.rstrip("/").split("/")[-1]
            if slug in seen:
                continue
            seen.add(slug)

            container = a.find_parent(["article", "li", "div"]) or a
            text = container.get_text(" ", strip=True)
            title_el = container.find(["h2", "h3", "h4"])
            title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
            if not title:
                continue
            loc_el = container.find(class_=re.compile(r"loc|country", re.I))
            loc_str = loc_el.get_text(" ", strip=True) if loc_el else ""

            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=slug,
                    title=title,
                    official_source_url=full_url,
                    raw_apply_url=full_url,
                    location_raw=loc_str,
                    description_text=text,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        return listings


class TelusAIConnector(BaseConnector):
    MAX_PAGES = 5

    def fetch_listings(self) -> List[RawJobListing]:
        listings: List[RawJobListing] = []
        seen = set()

        for page in range(1, self.MAX_PAGES + 1):
            params = {"page": page} if page > 1 else None
            resp = self.http.get(
                self.api_endpoint,
                params=params,
                timeout_sec=self.timeout_sec,
                rate_limit_rps=self.rate_limit_rps,
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            page_found = 0

            for ld in extract_json_ld_job_postings(soup):
                url = str(ld.get("url") or self.official_url)
                jid = str(ld.get("reqId") or url.rstrip("/").split("/")[-1])
                if jid in seen:
                    continue
                seen.add(jid)
                page_found += 1
                loc_str = ""
                jloc = ld.get("jobLocation")
                if isinstance(jloc, dict):
                    addr = jloc.get("address") or {}
                    if isinstance(addr, dict):
                        loc_str = ", ".join(
                            str(addr.get(k) or "") for k in ("addressLocality", "addressRegion", "addressCountry") if addr.get(k)
                        )
                listings.append(
                    RawJobListing(
                        source_connector=self.source_id,
                        platform=self.platform,
                        source_job_id=jid,
                        title=str(ld.get("title") or ""),
                        official_source_url=url,
                        raw_apply_url=url,
                        location_raw=loc_str,
                        description_text=str(ld.get("description") or ""),
                        official_posted_at=str(ld.get("datePosted")) if ld.get("datePosted") else None,
                        logo_url=self.logo,
                        pure_ai_eval_board=self.pure_ai_eval_board,
                    )
                )

            for a in soup.select("a[href*='/job/']"):
                href = (a.get("href") or "").strip()
                full_url = urljoin("https://jobs.telusdigital.com", href)
                container = a.find_parent(["li", "article", "div"]) or a
                text = container.get_text(" ", strip=True)
                m_req = re.search(r"\b(Req[A-Z0-9\-]+)\b", text)
                jid = m_req.group(1) if m_req else full_url.rstrip("/").split("/")[-1]
                if jid in seen:
                    continue
                seen.add(jid)
                page_found += 1
                title = a.get_text(" ", strip=True)
                loc_el = container.find(class_=re.compile(r"location|city|country", re.I))
                loc_str = loc_el.get_text(" ", strip=True) if loc_el else ""
                listings.append(
                    RawJobListing(
                        source_connector=self.source_id,
                        platform=self.platform,
                        source_job_id=jid,
                        title=title,
                        official_source_url=full_url,
                        raw_apply_url=full_url,
                        location_raw=loc_str,
                        description_text=text,
                        logo_url=self.logo,
                        pure_ai_eval_board=self.pure_ai_eval_board,
                    )
                )

            if page_found == 0 or not soup.select_one("a[rel='next'], a.next"):
                break

        return listings


class MindriftConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        resp = self.http.get(self.api_endpoint, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp.text, "html.parser")
        listings: List[RawJobListing] = []
        seen = set()

        for card in soup.select("[data-project-id], article, li.project-card, a[href*='mindrift.ai']"):
            title_el = card.find(["h2", "h3", "h4"])
            if not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            if not title or title in seen:
                continue
            seen.add(title)

            text = card.get_text(" ", strip=True)
            a_el = card if card.name == "a" else card.find("a", href=True)
            href = a_el["href"] if a_el and a_el.get("href") else self.official_url
            full_url = urljoin("https://mindrift.ai", href)
            jid = card.get("data-project-id") or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]

            m_pay = re.search(r"(?:up\s+to\s+)?\$\s*\d+(?:\s*-\s*\$?\d+)?\s*/\s*(?:hr|hour)", text, re.IGNORECASE)
            comp_raw = m_pay.group(0) if m_pay else ""

            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=str(jid),
                    title=title,
                    official_source_url=self.official_url,
                    raw_apply_url=full_url,
                    location_raw="",
                    description_text=text,
                    compensation_raw=comp_raw,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        return listings


class IMeritConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        resp = self.http.get(self.api_endpoint, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp.text, "html.parser")
        listings: List[RawJobListing] = []
        seen = set()

        for card in soup.select("article, div.role-card, div.career-card, li.job-item"):
            title_el = card.find(["h2", "h3", "h4"])
            if not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            a_el = card.find("a", href=True)
            href = a_el["href"] if a_el else self.official_url
            full_url = urljoin("https://imerit.ai", href)
            jid = full_url.rstrip("/").split("/")[-1]
            if not jid or jid == "careers-listing":
                jid = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
            if jid in seen:
                continue
            seen.add(jid)

            text = card.get_text(" ", strip=True)
            loc_el = card.find(class_=re.compile(r"loc", re.I))
            loc_str = loc_el.get_text(" ", strip=True) if loc_el else ""
            if not loc_str:
                m_pin = re.search(r"📍\s*([^⏱🌐\n]+)", text)
                if m_pin:
                    loc_str = m_pin.group(1).strip()

            emp = "Independent Contractor (Scholar)" if "independent contractor" in text.lower() or "scholar" in text.lower() else ""

            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=title,
                    official_source_url=full_url,
                    raw_apply_url=full_url,
                    location_raw=loc_str,
                    description_text=text,
                    employment_type=emp,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        return listings
