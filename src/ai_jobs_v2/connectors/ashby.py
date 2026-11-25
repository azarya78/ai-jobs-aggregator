"""Ashby Public Job Posting API Connector."""
from __future__ import annotations
from typing import List
from .base import BaseConnector
from ..models import RawJobListing


class AshbyConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        resp = self.http.get(
            self.api_endpoint,
            timeout_sec=self.timeout_sec,
            rate_limit_rps=self.rate_limit_rps,
        )
        payload = resp.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError(f"Invalid Ashby API payload for {self.source_id}")

        results: List[RawJobListing] = []
        for item in payload["jobs"]:
            if not isinstance(item, dict):
                continue
            if item.get("isListed") is False:
                continue
            jid = str(item.get("id") or "").strip()
            title = str(item.get("title") or "").strip()
            if not jid or not title:
                continue

            locs = [str(item.get("location") or "").strip()]
            for sec in item.get("secondaryLocations") or []:
                if isinstance(sec, dict) and sec.get("location"):
                    locs.append(str(sec["location"]).strip())
            if item.get("isRemote") and not any("remote" in l.lower() for l in locs):
                locs.append("Remote")
            loc_str = "; ".join(l for l in locs if l)

            comp_obj = item.get("compensation") or {}
            comp_raw = str(comp_obj.get("compensationTierSummary") or "") if isinstance(comp_obj, dict) else ""

            job_url = str(item.get("jobUrl") or f"{self.official_url.rstrip('/')}/{jid}").strip()
            apply_url = str(item.get("applyUrl") or job_url).strip()

            results.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=title,
                    official_source_url=job_url,
                    raw_apply_url=apply_url,
                    location_raw=loc_str,
                    description_text=str(item.get("descriptionPlain") or item.get("descriptionHtml") or ""),
                    compensation_raw=comp_raw,
                    official_posted_at=str(item["publishedAt"]) if item.get("publishedAt") else None,
                    employment_type=str(item.get("employmentType") or ""),
                    department=str(item.get("department") or item.get("team") or ""),
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                )
            )
        return results
