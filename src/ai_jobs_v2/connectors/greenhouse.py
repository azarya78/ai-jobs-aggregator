"""Greenhouse Job Board API Connector."""
from __future__ import annotations
from typing import List
from .base import BaseConnector
from ..models import RawJobListing


class GreenhouseConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        resp = self.http.get(
            self.api_endpoint,
            timeout_sec=self.timeout_sec,
            rate_limit_rps=self.rate_limit_rps,
        )
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
            posted_at = item.get("first_published") or item.get("updated_at")

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
