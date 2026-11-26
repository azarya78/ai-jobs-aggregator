"""Greenhouse Job Board API Connector (with public SSR fallback probe for migrated boards)."""
from __future__ import annotations
import re
from typing import List
from urllib.parse import urljoin
from .base import BaseConnector
from ..models import RawJobListing


class GreenhouseConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        try:
            resp = self.http.get(
                self.api_endpoint,
                timeout_sec=self.timeout_sec,
                rate_limit_rps=self.rate_limit_rps,
            )
        except Exception as api_exc:
            fallback_url = self.config.get("public_fallback_url")
            if fallback_url and "HTTP 404" in str(api_exc):
                fallback_listings = self._probe_public_opportunities_fallback(fallback_url, str(api_exc))
                if fallback_listings:
                    return fallback_listings
            raise

        payload = resp.json()
        if not isinstance(payload, dict) or "jobs" not in payload or not isinstance(payload["jobs"], list):
            raise ValueError(f"Invalid Greenhouse API response for {self.source_id}")

        apply_tpl = self.config.get("canonical_apply_template")
        results: List[RawJobListing] = []

        for item in payload["jobs"]:
            if not isinstance(item, dict):
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not jid or not title:
                continue

            loc_obj = item.get("location") or {}
            loc_str = str(loc_obj.get("name") or "").strip() if isinstance(loc_obj, dict) else ""

            depts = item.get("departments") or []
            dept_names = ", ".join(
                str(d.get("name") or "") for d in depts if isinstance(d, dict) and d.get("name")
            )

            comp_raw = ""
            pay_ranges = item.get("pay_input_ranges") or []
            if isinstance(pay_ranges, list) and pay_ranges:
                pr = pay_ranges[0]
                if isinstance(pr, dict):
                    cmin = pr.get("min_cents")
                    cmax = pr.get("max_cents")
                    curr = pr.get("currency_type") or "USD"
                    if cmin is not None and cmax is not None:
                        comp_raw = f"{curr} {cmin / 100:.2f} - {cmax / 100:.2f}"

            abs_url = str(item.get("absolute_url") or self.official_url).strip()
            apply_url = apply_tpl.format(job_id=jid) if apply_tpl else abs_url
            posted_at = item.get("first_published")  # An update is not a publication.

            results.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=title,
                    official_source_url=abs_url,
                    raw_apply_url=apply_url,
                    location_raw=loc_str,
                    description_text=str(item.get("content") or ""),
                    compensation_raw=comp_raw,
                    official_posted_at=str(posted_at) if posted_at else None,
                    department=dept_names,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        return results

    def _probe_public_opportunities_fallback(self, fallback_url: str, orig_err: str) -> List[RawJobListing]:
        """Legitimate unauthenticated SSR probe if a Greenhouse board migrated to a public web page."""
        from bs4 import BeautifulSoup

        resp = self.http.get(fallback_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp.text, "html.parser")
        listings: List[RawJobListing] = []
        seen = set()

        for a in soup.select("a[href*='/opportunities/']"):
            href = (a.get("href") or "").strip()
            full_url = urljoin(fallback_url, href)
            m = re.search(r"/opportunities/(\d{6,})", full_url)
            if not m:
                continue
            jid = m.group(1)
            if jid in seen:
                continue
            seen.add(jid)
            container = a if len(a.get_text(strip=True)) > 20 else (a.find_parent(["div", "li", "article"]) or a)
            text = container.get_text(" ", strip=True)
            title_el = container.find(["h2", "h3", "h4"])
            title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
            if not title:
                continue
            m_loc = re.search(r"(Remote\s*-\s*[^\n$]+?)(?=\s+(?:Language|Code|Generalist|Math|\d+\s+engaged)|$)", text)
            loc_str = m_loc.group(1).strip() if m_loc else "Remote"
            m_pay = re.search(r"(?:Up\s+to\s+)?\$\d+(?:\.\d+)?\s*(?:USD)?\s*/\s*hr", text, re.IGNORECASE)
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
                    compensation_raw=m_pay.group(0) if m_pay else "",
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )

        if not listings:
            raise RuntimeError(
                f"{orig_err}; fallback {fallback_url} requires client-side session/CSRF token (0 public SSR listings)"
            )
        return listings
