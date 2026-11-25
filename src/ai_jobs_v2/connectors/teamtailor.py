"""Teamtailor RSS + Public HTML/JSON-LD Connector (DataForce by TransPerfect)."""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from typing import List
from .base import BaseConnector
from ..models import RawJobListing


class TeamtailorConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        try:
            resp = self.http.get(
                self.api_endpoint,
                timeout_sec=self.timeout_sec,
                rate_limit_rps=self.rate_limit_rps,
            )
            if "<rss" in resp.text or "<item>" in resp.text:
                return self._parse_rss(resp.text)
        except Exception:
            if not self.config.get("html_endpoint"):
                raise

        html_url = self.config.get("html_endpoint") or self.official_url
        resp_html = self.http.get(
            html_url,
            timeout_sec=self.timeout_sec,
            rate_limit_rps=self.rate_limit_rps,
        )
        return self._parse_html(resp_html.text, html_url)

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
                m_loc = re.search(r"Work\s+Location\s*:\s*([^\n<]+)", desc, re.IGNORECASE)
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
        for a in soup.select("a[href*='/jobs/']"):
            href = (a.get("href") or "").strip()
            if not re.search(r"/jobs/\d+", href):
                continue
            full_url = href if href.startswith("http") else f"https://community.transperfect.com{href}"
            title = a.get_text(" ", strip=True)
            m_id = re.search(r"/jobs/([0-9a-zA-Z\-]+)", full_url)
            jid = m_id.group(1) if m_id else full_url
            card_text = a.parent.get_text(" ", strip=True) if a.parent else title
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
