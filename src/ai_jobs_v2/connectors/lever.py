"""Lever Postings API v0 Connector (with skip/limit pagination)."""
from __future__ import annotations
from typing import List, Dict, Any
from .base import BaseConnector
from ..models import RawJobListing


class LeverConnector(BaseConnector):
    PAGE_SIZE = 100
    MAX_PAGES = 20

    def fetch_listings(self) -> List[RawJobListing]:
        results: List[RawJobListing] = []
        skip = 0

        for _ in range(self.MAX_PAGES):
            resp = self.http.get(
                self.api_endpoint,
                params={"mode": "json", "limit": self.PAGE_SIZE, "skip": skip},
                timeout_sec=self.timeout_sec,
                rate_limit_rps=self.rate_limit_rps,
            )
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"Expected JSON array from Lever API for {self.source_id}")

            if not data:
                break

            for item in data:
                if not isinstance(item, dict):
                    continue
                jid = str(item.get("id") or "").strip()
                title = str(item.get("text") or "").strip()
                if not jid or not title:
                    continue

                cats: Dict[str, Any] = item.get("categories") or {}
                loc_parts = [
                    str(cats.get("location") or "").strip(),
                    str(item.get("country") or "").strip(),
                    str(item.get("workplaceType") or "").strip(),
                ]
                loc_str = " · ".join(dict.fromkeys([p for p in loc_parts if p and p.lower() != "unspecified"]))

                desc_chunks = [
                    str(item.get("descriptionPlain") or item.get("description") or ""),
                ]
                for lst in item.get("lists") or []:
                    if isinstance(lst, dict):
                        desc_chunks.append(f"{lst.get('text', '')}: {lst.get('content', '')}")
                desc_chunks.append(str(item.get("additionalPlain") or item.get("additional") or ""))
                full_desc = "\n".join(c for c in desc_chunks if c)

                comp_raw = ""
                sal = item.get("salaryRange")
                if isinstance(sal, dict) and (sal.get("min") or sal.get("max")):
                    cur = sal.get("currency") or "USD"
                    interval = sal.get("interval") or ""
                    smin, smax = sal.get("min"), sal.get("max")
                    if smin and smax and smin != smax:
                        comp_raw = f"{cur} {smin} - {smax} / {interval}".strip(" /")
                    else:
                        comp_raw = f"{cur} {smax or smin} / {interval}".strip(" /")

                hosted_url = str(item.get("hostedUrl") or self.official_url).strip()
                apply_url = str(item.get("applyUrl") or hosted_url).strip()
                created_at = str(item["createdAt"]) if item.get("createdAt") is not None else None

                results.append(
                    RawJobListing(
                        source_connector=self.source_id,
                        platform=self.platform,
                        source_job_id=jid,
                        title=title,
                        official_source_url=hosted_url,
                        raw_apply_url=apply_url,
                        location_raw=loc_str,
                        description_text=full_desc,
                        compensation_raw=comp_raw,
                        official_posted_at=created_at,
                        employment_type=str(cats.get("commitment") or ""),
                        department=str(cats.get("department") or cats.get("team") or ""),
                        logo_url=self.logo,
                        pure_ai_eval_board=self.pure_ai_eval_board,
                    )
                )

            if len(data) < self.PAGE_SIZE:
                break
            skip += self.PAGE_SIZE

        return results
