"""SmartRecruiters Public Postings API Connector (Appen Crowd & Linguistic Roles)."""
from __future__ import annotations
from typing import List
from .base import BaseConnector
from ..models import RawJobListing


class SmartRecruitersConnector(BaseConnector):
    PAGE_SIZE = 100
    MAX_PAGES = 10
    MAX_DETAIL_HYDRATIONS = 60

    def fetch_listings(self) -> List[RawJobListing]:
        results: List[RawJobListing] = []
        offset = 0
        hydrated = 0

        for _ in range(self.MAX_PAGES):
            resp = self.http.get(
                self.api_endpoint,
                params={"limit": self.PAGE_SIZE, "offset": offset},
                timeout_sec=self.timeout_sec,
                rate_limit_rps=self.rate_limit_rps,
            )
            payload = resp.json()
            content = payload.get("content") if isinstance(payload, dict) else None
            if not isinstance(content, list):
                raise ValueError(f"Invalid SmartRecruiters API payload for {self.source_id}")

            if not content:
                break

            for item in content:
                if not isinstance(item, dict):
                    continue
                jid = str(item.get("id") or item.get("uuid") or "").strip()
                title = str(item.get("name") or "").strip()
                if not jid or not title:
                    continue

                loc = item.get("location") or {}
                loc_parts = [
                    str(loc.get("city") or "").strip(),
                    str(loc.get("region") or "").strip(),
                    str(loc.get("country") or "").strip(),
                ]
                if loc.get("remote"):
                    loc_parts.insert(0, "Remote")
                loc_str = ", ".join(p for p in loc_parts if p)

                dept_obj = item.get("department") or {}
                dept_str = str(dept_obj.get("label") or "") if isinstance(dept_obj, dict) else ""
                emp_obj = item.get("typeOfEmployment") or {}
                emp_str = str(emp_obj.get("label") or "") if isinstance(emp_obj, dict) else ""

                posting_url = f"https://jobs.smartrecruiters.com/Appen/{jid}"
                apply_url = posting_url
                desc_text = title

                if hydrated < self.MAX_DETAIL_HYDRATIONS:
                    detail_url = f"{self.api_endpoint.rstrip('/')}/{jid}"
                    try:
                        d_resp = self.http.get(
                            detail_url,
                            timeout_sec=self.timeout_sec,
                            rate_limit_rps=self.rate_limit_rps,
                        )
                        d_json = d_resp.json()
                        hydrated += 1
                        if isinstance(d_json, dict):
                            posting_url = str(d_json.get("postingUrl") or posting_url)
                            apply_url = str(d_json.get("applyUrl") or posting_url)
                            sections = ((d_json.get("jobAd") or {}).get("sections") or {})
                            sec_texts = []
                            for sec_key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
                                sec_obj = sections.get(sec_key)
                                if isinstance(sec_obj, dict) and sec_obj.get("text"):
                                    sec_texts.append(str(sec_obj["text"]))
                            if sec_texts:
                                desc_text = "\n".join(sec_texts)
                    except Exception:
                        pass

                results.append(
                    RawJobListing(
                        source_connector=self.source_id,
                        platform=self.platform,
                        source_job_id=jid,
                        title=title,
                        official_source_url=posting_url,
                        raw_apply_url=apply_url,
                        location_raw=loc_str,
                        description_text=desc_text,
                        official_posted_at=str(item["releasedDate"]) if item.get("releasedDate") else None,
                        employment_type=emp_str,
                        department=dept_str,
                        logo_url=self.logo,
                        pure_ai_eval_board=self.pure_ai_eval_board,
                    )
                )

            total_found = int(payload.get("totalFound") or 0)
            offset += len(content)
            if len(content) < self.PAGE_SIZE or (total_found and offset >= total_found):
                break

        return results
