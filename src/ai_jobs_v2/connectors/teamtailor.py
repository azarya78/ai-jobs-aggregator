"""Teamtailor RSS + Paginated Public HTML/JSON-LD Connector (DataForce by TransPerfect)."""
from __future__ import annotations
import json
import re
import xml.etree.ElementTree as ET
from typing import List, Set
from urllib.parse import urljoin
from .base import BaseConnector
from ..models import RawJobListing


class TeamtailorConnector(BaseConnector):
    RSS_CAP_THRESHOLD = 100
    MAX_HTML_PAGES = 8
    MAX_DETAIL_HYDRATIONS = 20

    def fetch_listings(self) -> List[RawJobListing]:
        listings: List[RawJobListing] = []
        seen_ids: Set[str] = set()

        try:
            resp = self.http.get(
                self.api_endpoint,
                timeout_sec=self.timeout_sec,
                rate_limit_rps=self.rate_limit_rps,
            )
            if "<rss" in resp.text or "<item>" in resp.text:
                rss_items = self._parse_rss(resp.text)
                for item in rss_items:
                    if item.source_job_id not in seen_ids:
                        seen_ids.add(item.source_job_id)
                        listings.append(item)
                # Teamtailor RSS truncates at 100 items; supplement via paginated HTML when html_endpoint is configured
                if len(rss_items) >= self.RSS_CAP_THRESHOLD and self.config.get("html_endpoint"):
                    self._supplement_from_paginated_html(listings, seen_ids)
                return listings
        except Exception:
            if not self.config.get("html_endpoint"):
                raise

        html_url = self.config.get("html_endpoint") or self.official_url
        self._supplement_from_paginated_html(listings, seen_ids, base_html_url=html_url)
        return listings

    def _supplement_from_paginated_html(
        self,
        listings: List[RawJobListing],
        seen_ids: Set[str],
        base_html_url: str = "",
    ) -> None:
        html_url = base_html_url or self.config.get("html_endpoint") or self.official_url
        hydrated = 0

        for page in range(1, self.MAX_HTML_PAGES + 1):
            try:
                params = {"page": page} if page > 1 else None
                resp_html = self.http.get(
                    html_url,
                    params=params,
                    timeout_sec=self.timeout_sec,
                    rate_limit_rps=self.rate_limit_rps,
                )
            except Exception:
                if page == 1 and not listings:
                    raise
                break

            page_cards = self._parse_html(resp_html.text, html_url)
            new_on_page = 0
            for card in page_cards:
                if card.source_job_id in seen_ids:
                    continue
                seen_ids.add(card.source_job_id)
                new_on_page += 1

                if hydrated < self.MAX_DETAIL_HYDRATIONS and re.search(
                    r"\b(indonesia|indonesian|global|worldwide|remote|rater|evaluat|annotat|sme|ai|audio|speech|search)\b",
                    f"{card.title} {card.location_raw} {card.description_text}",
                    re.IGNORECASE,
                ):
                    try:
                        d_resp = self.http.get(
                            card.official_source_url,
                            timeout_sec=self.timeout_sec,
                            rate_limit_rps=self.rate_limit_rps,
                        )
                        hydrated += 1
                        self._enrich_card_from_detail_html(card, d_resp.text)
                    except Exception:
                        pass

                listings.append(card)

            if new_on_page == 0 or f"page={page + 1}" not in resp_html.text:
                break

    @staticmethod
    def _enrich_card_from_detail_html(card: RawJobListing, detail_html: str) -> None:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(detail_html, "html.parser")
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw_txt = (script.string or script.get_text() or "").strip()
            if not raw_txt:
                continue
            try:
                ld = json.loads(raw_txt)
            except Exception:
                continue
            if isinstance(ld, dict) and ld.get("@type") == "JobPosting":
                if ld.get("description"):
                    card.description_text = str(ld["description"])
                if ld.get("datePosted") and not card.official_posted_at:
                    card.official_posted_at = str(ld["datePosted"])
                jloc = ld.get("jobLocation")
                if isinstance(jloc, dict):
                    addr = jloc.get("address") or {}
                    if isinstance(addr, dict):
                        loc_str = ", ".join(
                            str(addr.get(k) or "")
                            for k in ("addressLocality", "addressRegion", "addressCountry")
                            if addr.get(k)
                        )
                        if loc_str:
                            card.location_raw = loc_str
                if not card.location_raw:
                    m_loc = re.search(r"Work\s+Location\s*:\s*([^\n<.;]+)", card.description_text, re.IGNORECASE)
                    if m_loc:
                        card.location_raw = m_loc.group(1).strip()
                return

    def _parse_rss(self, xml_text: str) -> List[RawJobListing]:
        root = ET.fromstring(xml_text)
        listings: List[RawJobListing] = []
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            guid = (item.findtext("guid") or "").strip()
            desc = (item.findtext("description") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip() or None

            if not title or not link:
                continue

            m_id = re.search(r"/jobs/([0-9a-zA-Z\-]+)", link)
            jid = m_id.group(1) if m_id else (guid or link)

            loc_raw = ""
            for child in item:
                tag_low = child.tag.lower()
                if tag_low.endswith("location") or tag_low.endswith("region") or tag_low.endswith("country"):
                    if child.text and child.text.strip():
                        loc_raw = f"{loc_raw}, {child.text.strip()}".strip(", ")

            if not loc_raw:
                m_loc = re.search(r"Work\s+Location\s*:\s*([^\n<.;]+)", desc, re.IGNORECASE)
                if m_loc:
                    loc_raw = m_loc.group(1).strip()

            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=title,
                    official_source_url=link,
                    raw_apply_url=link,
                    location_raw=loc_raw,
                    description_text=desc,
                    official_posted_at=pub_date,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        return listings

    def _parse_html(self, html_text: str, base_url: str) -> List[RawJobListing]:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html_text, "html.parser")
        listings: List[RawJobListing] = []
        seen = set()
        for a in soup.select("a[href*='/jobs/']"):
            href = (a.get("href") or "").strip()
            if not re.search(r"/jobs/\d+", href):
                continue
            full_url = urljoin(base_url or "https://community.transperfect.com", href)
            m_id = re.search(r"/jobs/([0-9a-zA-Z\-]+)", full_url)
            jid = m_id.group(1) if m_id else full_url
            if jid in seen:
                continue
            seen.add(jid)

            container = a.find_parent(["li", "article", "div"]) or a
            title_el = a.find(["span", "h2", "h3", "h4"])
            title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
            if not title:
                continue
            card_text = container.get_text(" ", strip=True)
            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=title,
                    official_source_url=full_url,
                    raw_apply_url=full_url,
                    description_text=card_text,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        return listings
