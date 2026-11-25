"""Public Career Page, Official Widget JSON & Next.js SSR Extractors (Mercor, Micro1, Alignerr, OneForma, TELUS AI, Mindrift, iMerit)."""
from __future__ import annotations
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, date
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
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


def _format_rate_range(
    rmin: Any,
    rmax: Any,
    period: str = "hr",
    currency_prefix: str = "$",
) -> str:
    try:
        vmin = float(rmin) if rmin not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        vmin = None
    try:
        vmax = float(rmax) if rmax not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        vmax = None

    if vmin is None and vmax is None:
        return ""

    def _fmt(v: float) -> str:
        return str(int(v)) if v == int(v) else f"{v:.2f}".rstrip("0").rstrip(".")

    unit_lbl = ""
    p_low = (period or "").lower()
    if p_low in ("h", "hr", "hour", "hourly", "per-hour-wage"):
        unit_lbl = "hr"
    elif "task" in p_low:
        unit_lbl = "task"
    elif "one" in p_low or "project" in p_low:
        unit_lbl = "project"
    elif "year" in p_low or "yr" in p_low or "annual" in p_low:
        unit_lbl = "yr"
    elif "month" in p_low or "mo" in p_low:
        unit_lbl = "month"

    suffix = f" / {unit_lbl}" if unit_lbl else ""
    if vmin is not None and vmax is not None and vmin != vmax:
        lo, hi = min(vmin, vmax), max(vmin, vmax)
        return f"{currency_prefix}{_fmt(lo)} - {currency_prefix}{_fmt(hi)}{suffix}"
    val = vmax if vmax is not None else vmin
    return f"{currency_prefix}{_fmt(val)}{suffix}"  # type: ignore[arg-type]


class MercorConnector(BaseConnector):
    MAX_SITEMAP_HYDRATIONS = 25

    @staticmethod
    def _walk_mercor_job_dicts(node: Any, out: List[Dict[str, Any]], depth: int = 0) -> None:
        if depth > 14 or node is None:
            return
        if isinstance(node, list):
            for elem in node:
                MercorConnector._walk_mercor_job_dicts(elem, out, depth + 1)
            return
        if isinstance(node, dict):
            raw_id = str(node.get("listingId") or node.get("jobId") or node.get("id") or "").strip()
            raw_title = str(node.get("title") or node.get("roleTitle") or node.get("name") or "").strip()
            has_mercor_shape = bool(
                raw_title
                and (
                    raw_id.startswith("list_")
                    or (
                        raw_id
                        and any(
                            k in node
                            for k in (
                                "rateMin",
                                "rate_min",
                                "hourlyRate",
                                "rate",
                                "pay",
                                "commitment",
                                "statusText",
                                "isClosed",
                                "companyBrandVisible",
                                "remainingSlots",
                            )
                        )
                    )
                )
            )
            if has_mercor_shape:
                out.append(node)
                return
            for v in node.values():
                if isinstance(v, (dict, list)):
                    MercorConnector._walk_mercor_job_dicts(v, out, depth + 1)

    def _build_from_mercor_dict(self, jb: Dict[str, Any]) -> Optional[RawJobListing]:
        jid = str(jb.get("listingId") or jb.get("jobId") or jb.get("id") or "").strip()
        title = str(jb.get("title") or jb.get("roleTitle") or jb.get("name") or "").strip()
        if not jid or not title:
            return None

        slug = str(jb.get("slug") or "").strip("/")
        if jb.get("url"):
            url = urljoin("https://work.mercor.com", str(jb["url"]))
        elif slug:
            url = f"https://work.mercor.com/jobs/{jid}/{slug}"
        else:
            url = f"https://work.mercor.com/jobs/{jid}"

        status_str = f"{jb.get('status', '')} {jb.get('statusText', '')}".lower()
        closed = (
            bool(jb.get("isClosed"))
            or jb.get("acceptingApplications") is False
            or any(
                tok in status_str
                for tok in ("closed", "archived", "paused", "no longer accepting", "not accepting applications")
            )
        )

        loc_val = jb.get("location") or jb.get("workLocation") or jb.get("work_location") or jb.get("countries") or jb.get("eligibleCountries")
        if isinstance(loc_val, list):
            loc_str = ", ".join(str(x) for x in loc_val if x) or "Remote"
        else:
            loc_str = str(loc_val or "Remote").strip() or "Remote"

        comp_raw = str(jb.get("rate") or jb.get("pay") or jb.get("compensation") or "").strip()
        if isinstance(jb.get("rate"), (int, float)):
            comp_raw = _format_rate_range(jb.get("rate"), jb.get("rate"), str(jb.get("ratePeriod") or jb.get("rate_period") or ""))
        if not comp_raw:
            rmin = jb.get("rateMin") if jb.get("rateMin") is not None else jb.get("rate_min")
            rmax = jb.get("rateMax") if jb.get("rateMax") is not None else jb.get("rate_max")
            period = str(jb.get("ratePeriod") or jb.get("rate_period") or "")
            comp_raw = _format_rate_range(rmin, rmax, period)

        desc_parts = [
            str(jb.get("description") or jb.get("shortDescription") or jb.get("summary") or ""),
            str(jb.get("responsibilities") or ""),
            str(jb.get("requirements") or jb.get("qualifications") or ""),
            str(jb.get("domain") or jb.get("category") or ""),
        ]
        tags = jb.get("tags") or jb.get("skills") or []
        if isinstance(tags, list) and tags:
            desc_parts.append(" ".join(str(t) for t in tags if t))
        desc_text = "\n".join(p.strip() for p in desc_parts if p and p.strip())
        posted_raw = jb.get("createdAt") or jb.get("created_at") or jb.get("publishedAt") or jb.get("postedAt")
        return RawJobListing(
            source_connector=self.source_id,
            platform=self.platform,
            source_job_id=jid,
            title=title,
            official_source_url=url,
            raw_apply_url=url,
            location_raw=loc_str,
            description_text=desc_text,
            compensation_raw=comp_raw,
            official_posted_at=str(posted_raw) if posted_raw else None,
            employment_type=str(jb.get("commitment") or jb.get("type") or ""),
            department=str(jb.get("domain") or jb.get("category") or ""),
            logo_url=self.logo,
            pure_ai_eval_board=self.pure_ai_eval_board,
            is_explicitly_closed=closed,
        )

    def fetch_listings(self) -> List[RawJobListing]:
        target_url = self.config.get("html_endpoint") or self.official_url
        resp = self.http.get(target_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp.text, "html.parser")
        by_id: Dict[str, RawJobListing] = {}

        # 1. Extract complete dehydrated React Query / pageProps index from __NEXT_DATA__
        next_tag = soup.find("script", id="__NEXT_DATA__")
        if next_tag and (next_tag.string or next_tag.get_text()):
            try:
                nd = json.loads(next_tag.string or next_tag.get_text())
                candidates: List[Dict[str, Any]] = []
                self._walk_mercor_job_dicts(nd.get("props") or nd, candidates)
                for jb in candidates:
                    rec = self._build_from_mercor_dict(jb)
                    if rec and rec.source_job_id not in by_id:
                        by_id[rec.source_job_id] = rec
            except Exception:
                pass

        # 2. Extract Next.js App Router RSC flight chunks (`self.__next_f.push`) if present
        if not by_id and "__next_f" in resp.text:
            for script in soup.find_all("script"):
                stxt = script.string or script.get_text() or ""
                if "__next_f.push" not in stxt or "list_" not in stxt:
                    continue
                for m_str in re.finditer(r'self\.__next_f\.push\(\[\d+,\s*("(?:[^"\\]|\\.)*")\]\)', stxt):
                    try:
                        decoded = json.loads(m_str.group(1))
                        for m_obj in re.finditer(r'\{[^{}]*"listingId"\s*:\s*"list_[A-Za-z0-9_\-]+"[^{}]*\}', decoded):
                            jb = json.loads(m_obj.group(0))
                            rec = self._build_from_mercor_dict(jb)
                            if rec and rec.source_job_id not in by_id:
                                by_id[rec.source_job_id] = rec
                    except Exception:
                        continue

        # 3. Parse SSR HTML cards (merges or serves as primary when __NEXT_DATA__ is absent)
        for a in soup.select("a[href*='/jobs/'], a[href*='listingId=list_']"):
            href = (a.get("href") or "").strip()
            full_url = urljoin("https://work.mercor.com", href)
            m = re.search(r"(?:/jobs/|listingId=)(list_[A-Za-z0-9_\-]+)", full_url)
            jid = m.group(1) if m else full_url
            if jid in by_id:
                continue

            container = a if len(a.get_text(strip=True)) > 20 else (a.find_parent(["li", "article", "div"]) or a)
            card_text = container.get_text(" ", strip=True)
            title_el = container.find(["h2", "h3", "h4", "strong"])
            title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
            if not title or title.lower() in ("apply", "view all opportunities", "explore all opportunities →"):
                continue

            closed = any(
                tok in card_text.lower()
                for tok in ("no longer accepting applications", "not accepting applications currently")
            )
            m_pay = re.search(r"\$\d+(?:\s*-\s*\$?\d+|\s*-\s*\d+)?\s*/\s*(?:hour|hr|task)", card_text, re.IGNORECASE)
            comp_raw = m_pay.group(0) if m_pay else ""

            by_id[jid] = RawJobListing(
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

        # 4. Sitemap discovery supplement ONLY when /explore returned multi-card SSR slice (10..35 items)
        # rather than the full ~200+ dehydrated index (keeps 1-card unit tests to a single HTTP request).
        sitemap_url = self.config.get("api_endpoint") or ""
        if 10 <= len(by_id) <= 35 and sitemap_url.endswith("sitemap.xml"):
            self._supplement_from_sitemap(sitemap_url, by_id)

        return list(by_id.values())

    def _supplement_from_sitemap(self, sitemap_url: str, by_id: Dict[str, RawJobListing]) -> None:
        try:
            s_resp = self.http.get(sitemap_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
            root = ET.fromstring(s_resp.text)
        except Exception:
            return

        priority_urls: List[Tuple[int, str, str]] = []
        for elem in root.iter():
            if elem.tag.lower().endswith("loc") and elem.text:
                loc_url = elem.text.strip()
                m = re.search(r"/jobs/(list_[A-Za-z0-9_\-]+)(?:/([^/?#]+))?", loc_url)
                if not m:
                    continue
                jid, slug = m.group(1), (m.group(2) or "").lower()
                if jid in by_id:
                    continue
                # Prioritize Indonesia, Global, Multilingual, and AI Evaluation slugs for bounded hydration
                if "indonesi" in slug or "bahasa" in slug:
                    prio = 0
                elif any(k in slug for k in ("global", "worldwide", "bilingual", "multilingual", "linguist", "generalist")):
                    prio = 1
                elif any(k in slug for k in ("evaluat", "rubric", "annotat", "safety", "audio", "voice", "search", "rater", "prompt", "ai")):
                    prio = 2
                else:
                    continue
                priority_urls.append((prio, jid, loc_url))

        priority_urls.sort(key=lambda x: x[0])
        for _, jid, detail_url in priority_urls[: self.MAX_SITEMAP_HYDRATIONS]:
            try:
                d_resp = self.http.get(detail_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
                d_soup = BeautifulSoup(d_resp.text, "html.parser")
                h1 = d_soup.find(["h1", "h2"])
                title = h1.get_text(" ", strip=True) if h1 else ""
                if not title and d_soup.title:
                    title = re.sub(r"\s*-\s*Mercor.*$", "", d_soup.title.get_text(" ", strip=True))
                if not title:
                    continue
                full_text = d_soup.get_text(" ", strip=True)
                closed = any(
                    tok in full_text.lower()
                    for tok in ("no longer accepting applications", "this listing is not accepting applications currently")
                )
                m_pay = re.search(r"\$\d+(?:\s*-\s*\$?\d+)?\s*/\s*(?:hr|hour|task)", full_text, re.IGNORECASE)
                m_loc = re.search(r"\b(Remote\s*[—\-]\s*[^\n·$]{3,50}|Indonesia)", full_text)
                by_id[jid] = RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=title,
                    official_source_url=detail_url,
                    raw_apply_url=detail_url,
                    location_raw=m_loc.group(1).strip() if m_loc else "Remote",
                    description_text=full_text[:6000],
                    compensation_raw=m_pay.group(0) if m_pay else "",
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                    is_explicitly_closed=closed,
                )
            except Exception:
                continue


class Micro1Connector(BaseConnector):
    PORTAL_API_DEFAULT = "https://prod-api.micro1.ai/api/v1/job/portal"
    MAX_PORTAL_PAGES = 8
    MAX_DETAIL_HYDRATIONS = 12

    @staticmethod
    def _extract_portal_items(payload: Any) -> Tuple[List[Dict[str, Any]], Optional[int]]:
        if isinstance(payload, list):
            return [x for x in payload if isinstance(x, dict)], None
        if isinstance(payload, dict):
            total = payload.get("total") or payload.get("totalCount") or payload.get("count")
            for key in ("data", "jobs", "results", "items", "rows", "records"):
                val = payload.get(key)
                if isinstance(val, list):
                    return [x for x in val if isinstance(x, dict)], (int(total) if isinstance(total, int) else None)
                if isinstance(val, dict):
                    sub_total = val.get("total") or val.get("totalCount") or total
                    for sub_key in ("jobs", "items", "results", "rows", "records", "data"):
                        sub_val = val.get(sub_key)
                        if isinstance(sub_val, list):
                            return [x for x in sub_val if isinstance(x, dict)], (
                                int(sub_total) if isinstance(sub_total, int) else None
                            )
        return [], None

    def _fetch_from_portal_api(self) -> List[RawJobListing]:
        portal_url = self.config.get("portal_api_endpoint") or self.PORTAL_API_DEFAULT
        headers = {
            "Origin": "https://www.micro1.ai",
            "Referer": "https://www.micro1.ai/experts/opportunities",
        }
        listings: List[RawJobListing] = []
        seen_ids: Set[str] = set()

        for page in range(1, self.MAX_PORTAL_PAGES + 1):
            body: Dict[str, Any] = {} if page == 1 else {"page": page, "limit": 100}
            try:
                resp = self.http.post(
                    portal_url,
                    json_body=body,
                    headers=headers,
                    timeout_sec=self.timeout_sec,
                    rate_limit_rps=self.rate_limit_rps,
                )
            except Exception:
                if page == 1:
                    # Retry once with explicit pagination filter body in case `{}` returned HTTP 400/422
                    resp = self.http.post(
                        portal_url,
                        json_body={"page": 1, "limit": 500},
                        headers=headers,
                        timeout_sec=self.timeout_sec,
                        rate_limit_rps=self.rate_limit_rps,
                    )
                else:
                    break

            items, total_count = self._extract_portal_items(resp.json())
            if not items:
                break

            new_on_page = 0
            for item in items:
                jid = str(
                    item.get("job_id")
                    or item.get("jobId")
                    or item.get("post_id")
                    or item.get("uuid")
                    or item.get("id")
                    or item.get("_id")
                    or ""
                ).strip()
                title = str(
                    item.get("job_title")
                    or item.get("jobTitle")
                    or item.get("title")
                    or item.get("role_title")
                    or item.get("name")
                    or ""
                ).strip()
                if not jid or not title or jid in seen_ids:
                    continue

                status_low = str(item.get("status") or "").lower()
                is_closed = item.get("is_active") is False or status_low in ("closed", "inactive", "archived", "draft")

                seen_ids.add(jid)
                new_on_page += 1

                raw_url = str(item.get("job_url") or item.get("apply_url") or item.get("url") or "").strip()
                if raw_url and "micro1.ai" in raw_url:
                    apply_url = raw_url
                else:
                    apply_url = f"https://jobs.micro1.ai/post/{jid}"

                skills_raw = item.get("skills") or item.get("required_skills") or item.get("tags") or []
                skill_names: List[str] = []
                if isinstance(skills_raw, list):
                    for s in skills_raw:
                        if isinstance(s, dict) and s.get("name"):
                            skill_names.append(str(s["name"]))
                        elif isinstance(s, str) and s.strip():
                            skill_names.append(s.strip())
                elif isinstance(skills_raw, str):
                    skill_names = [s.strip() for s in skills_raw.split(",") if s.strip()]

                comp_raw = str(
                    item.get("ideal_hourly_rate")
                    or item.get("hourly_rate")
                    or item.get("pay_rate")
                    or item.get("pay")
                    or item.get("rate")
                    or item.get("salary")
                    or ""
                ).strip()
                if not comp_raw:
                    pmin = item.get("min_pay") or item.get("pay_min") or item.get("min_rate") or item.get("rate_min") or item.get("minHourlyRate")
                    pmax = item.get("max_pay") or item.get("pay_max") or item.get("max_rate") or item.get("rate_max") or item.get("maxHourlyRate")
                    comp_raw = _format_rate_range(pmin, pmax, "hr")
                elif re.fullmatch(r"\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?", comp_raw):
                    comp_raw = f"${comp_raw}/hr"

                loc_val = item.get("location") or item.get("country") or item.get("countries") or "Remote"
                loc_str = ", ".join(str(x) for x in loc_val if x) if isinstance(loc_val, list) else str(loc_val)

                desc_raw = str(
                    item.get("description")
                    or item.get("job_description")
                    or item.get("summary")
                    or item.get("job_summary")
                    or ""
                ).strip()
                skills_clause = f"Required skills: {', '.join(skill_names)}." if skill_names else ""
                desc_full = "\n".join(
                    p for p in (desc_raw, skills_clause, "Use your expertise to train next generation AI models.") if p
                )

                posted_at = (
                    item.get("posted_at")
                    or item.get("date_posted")
                    or item.get("created_at")
                    or item.get("published_at")
                    or item.get("createdAt")
                )

                listings.append(
                    RawJobListing(
                        source_connector=self.source_id,
                        platform=self.platform,
                        source_job_id=jid,
                        title=title,
                        official_source_url=apply_url,
                        raw_apply_url=apply_url,
                        location_raw=loc_str or "Remote",
                        description_text=desc_full,
                        compensation_raw=comp_raw,
                        official_posted_at=str(posted_at) if posted_at else None,
                        logo_url=self.logo,
                        pure_ai_eval_board=self.pure_ai_eval_board,
                        is_explicitly_closed=is_closed,
                    )
                )

            # Stop if unpaginated (all roles returned at once) or final page reached
            if new_on_page == 0 or len(items) > 100 or (total_count is not None and len(seen_ids) >= total_count):
                break
            if page == 1 and total_count is None and len(items) not in (10, 12, 15, 20, 24, 25, 30, 50, 100):
                break

        # Hydrate detail pages on jobs.micro1.ai/post/{uuid} for Indonesian / Global candidates lacking descriptions
        hydrated = 0
        for rec in listings:
            if hydrated >= self.MAX_DETAIL_HYDRATIONS:
                break
            if len(rec.description_text) < 180 and re.search(
                r"\b(indonesi|bahasa|global|worldwide)\b", f"{rec.title} {rec.location_raw}", re.IGNORECASE
            ):
                try:
                    d_resp = self.http.get(rec.raw_apply_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
                    hydrated += 1
                    d_soup = BeautifulSoup(d_resp.text, "html.parser")
                    d_text = d_soup.get_text(" ", strip=True)
                    if len(d_text) > 200:
                        rec.description_text = d_text[:6000]
                        m_loc = re.search(r"Location\s*:\s*([^\n*]{2,60}?)(?=\s+(?:Job Summary|Scope of Work|About|$))", d_text)
                        if m_loc:
                            rec.location_raw = m_loc.group(1).strip()
                except Exception:
                    continue

        return listings

    def fetch_listings(self) -> List[RawJobListing]:
        if self.config.get("portal_api_endpoint", self.PORTAL_API_DEFAULT):
            try:
                portal_listings = self._fetch_from_portal_api()
                if portal_listings:
                    return portal_listings
            except Exception:
                pass

        resp = self.http.get(self.api_endpoint, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp.text, "html.parser")
        listings: List[RawJobListing] = []

        for ld in extract_json_ld_job_postings(soup):
            jid = str(ld.get("identifier", {}).get("value") if isinstance(ld.get("identifier"), dict) else ld.get("url") or "")
            url = str(ld.get("url") or self.official_url)
            m = re.search(r"/post/([0-9a-f\-]{8,})", url)
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

        seen: Set[str] = set()
        for card in soup.select("[data-job-id], a[href*='jobs.micro1.ai/post/'], a[href*='/post/']"):
            href = (card.get("href") or "").strip()
            full_url = urljoin("https://jobs.micro1.ai", href) if href else self.official_url
            m = re.search(r"/post/([0-9a-fA-F\-]{8,})", full_url)
            jid = card.get("data-job-id") or (m.group(1) if m else "")
            container = card if card.name in ("article", "div", "li") else (card.find_parent(["article", "li", "div"]) or card)
            text = container.get_text(" ", strip=True)
            title_el = container.find(["h2", "h3", "h4", "strong"])
            title = title_el.get_text(" ", strip=True) if title_el else a_title_fallback(card)
            if not title:
                continue
            key = jid or title
            if key in seen:
                continue
            seen.add(key)

            m_pay = re.search(r"Pay\s*:\s*([^\n]+?/(?:h|hr|hour))|\$\d+\s*-\s*\$?\d+\s*/\s*(?:h|hr|hour)", text, re.IGNORECASE)
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


def a_title_fallback(el: Any) -> str:
    txt = el.get_text(" ", strip=True) if el else ""
    if txt and txt.lower() not in ("apply", "apply now", "view job", "details"):
        return txt[:120]
    return ""


class AlignerrConnector(BaseConnector):
    def fetch_listings(self) -> List[RawJobListing]:
        # Probe structured public JSON API if configured or available, falling back to public HTML
        json_url = self.config.get("json_api_endpoint")
        if json_url:
            try:
                api_listings = self._fetch_from_json_api(json_url)
                if api_listings:
                    return api_listings
            except Exception:
                pass

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
            loc_str = str(loc_el["data-location"]).strip() if loc_el else ""
            if not loc_str:
                m_loc = re.search(r"\b(Remote\s*[-·]\s*[A-Za-z ,]+|Indonesia)\b", text)
                loc_str = m_loc.group(1).strip() if m_loc else "Remote"

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

    def _fetch_from_json_api(self, json_url: str) -> List[RawJobListing]:
        listings: List[RawJobListing] = []
        seen_ids: Set[str] = set()
        seen_role_loc_sig: Set[str] = set()
        offset = 0
        limit = 120

        for _ in range(5):
            resp = self.http.get(
                json_url,
                params={"limit": limit, "offset": offset},
                timeout_sec=self.timeout_sec,
                rate_limit_rps=self.rate_limit_rps,
            )
            payload = resp.json()
            items = payload if isinstance(payload, list) else (payload.get("jobs") or payload.get("data") or payload.get("items") or [])
            if not isinstance(items, list) or not items:
                break

            new_raw = 0
            for item in items:
                if not isinstance(item, dict):
                    continue
                jid = str(item.get("id") or item.get("slug") or item.get("uuid") or "").strip()
                title = str(item.get("title") or item.get("name") or "").strip()
                if not jid or not title or jid in seen_ids:
                    continue
                seen_ids.add(jid)
                new_raw += 1

                loc_str = str(item.get("location") or item.get("country") or "Remote").strip() or "Remote"
                # Collapse identical title + location city-clone spam on Alignerr
                sig = f"{title.lower()}|{loc_str.lower()}"
                if sig in seen_role_loc_sig:
                    continue
                seen_role_loc_sig.add(sig)

                slug = str(item.get("slug") or jid)
                url = str(item.get("url") or f"https://www.alignerr.com/jobs/{slug}")
                comp_raw = str(item.get("pay") or item.get("rate") or "").strip()
                if not comp_raw:
                    comp_raw = _format_rate_range(item.get("minPay") or item.get("pay_min"), item.get("maxPay") or item.get("pay_max"), "hr")

                listings.append(
                    RawJobListing(
                        source_connector=self.source_id,
                        platform=self.platform,
                        source_job_id=jid,
                        title=title,
                        official_source_url=url,
                        raw_apply_url=url,
                        location_raw=loc_str,
                        description_text=str(item.get("description") or title),
                        compensation_raw=comp_raw,
                        official_posted_at=str(item["createdAt"]) if item.get("createdAt") else None,
                        logo_url=self.logo,
                        pure_ai_eval_board=self.pure_ai_eval_board,
                    )
                )

            if new_raw < limit:
                break
            offset += limit

        return listings


class OneFormaConnector(BaseConnector):
    EXCLUDED_PROJECT_SLUGS = {
        "projects",
        "jobs",
        "job",
        "page",
        "country",
        "domain",
        "language",
        "type",
        "pay-type",
    }
    MAX_HTML_PAGES = 6

    @staticmethod
    def _slug_from_url(url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        parts = [p for p in path.split("/") if p]
        if not parts:
            return ""
        if len(parts) >= 2 and parts[-2] in ("page", "country", "domain", "language", "type"):
            return ""
        slug = parts[-1].lower()
        return "" if slug in OneFormaConnector.EXCLUDED_PROJECT_SLUGS else slug

    @staticmethod
    def _extract_oneforma_card_meta(card_text: str) -> Tuple[str, str]:
        loc_str = ""
        m_avail = re.search(
            r"((?:Available|Open)\s+in\s+\d+\s+countries\s+[A-Za-z ,·\-]+?)(?=\s+(?:Remote|\d+\s*(?:hr|to|min)|Flexible|About\s+\d+|Paid|\$|Apply)|$)",
            card_text,
            re.IGNORECASE,
        )
        if m_avail:
            loc_str = m_avail.group(1).strip()
        else:
            m_open = re.search(r"Open\s+to\s+(?:applicants|experts)\s+(?:living\s+)?in\s+([^\n.]+)", card_text, re.IGNORECASE)
            if m_open:
                loc_str = m_open.group(0).strip()

        comp_raw = ""
        m_pay = re.search(
            r"\$[\d.,]+(?:\s*(?:to|-|–)\s*\$?[\d.,]+)?\s*(?:USD\s*)?(?:per\s*(?:hour|session|approved\s+\w+|source\s+word|task)|/\s*(?:hr|hour))",
            card_text,
            re.IGNORECASE,
        )
        if m_pay:
            comp_raw = m_pay.group(0).strip()
        return loc_str, comp_raw

    def fetch_listings(self) -> List[RawJobListing]:
        by_slug: Dict[str, RawJobListing] = {}

        # 1. Query OneForma WordPress REST API (`wp/v2/job`)
        try:
            resp_api = self.http.get(self.api_endpoint, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
            data = resp_api.json()
            if isinstance(data, list):
                for wp_item in data:
                    if not isinstance(wp_item, dict):
                        continue
                    jid = str(wp_item.get("id") or "").strip()
                    t_obj = wp_item.get("title")
                    title = str(t_obj.get("rendered") if isinstance(t_obj, dict) else t_obj or "").strip()
                    link = str(wp_item.get("link") or self.official_url).strip()
                    slug = str(wp_item.get("slug") or self._slug_from_url(link) or jid).lower()
                    c_obj = wp_item.get("content") or wp_item.get("excerpt") or {}
                    desc = str(c_obj.get("rendered") if isinstance(c_obj, dict) else c_obj or "")

                    # Extract embedded WP taxonomy terms (countries, languages, domains) when _embed=1 is used
                    term_countries: List[str] = []
                    term_labels: List[str] = []
                    embedded = wp_item.get("_embedded") or {}
                    if isinstance(embedded, dict):
                        for term_group in embedded.get("wp:term") or []:
                            if not isinstance(term_group, list):
                                continue
                            for term in term_group:
                                if not isinstance(term, dict) or not term.get("name"):
                                    continue
                                tax = str(term.get("taxonomy") or "").lower()
                                tname = str(term["name"]).strip()
                                if "country" in tax or "location" in tax:
                                    term_countries.append(tname)
                                else:
                                    term_labels.append(tname)

                    loc_str = str(wp_item.get("location") or "").strip()
                    if not loc_str and term_countries:
                        loc_str = f"Available in {len(term_countries)} countries " + " ".join(term_countries)
                    if not loc_str:
                        loc_from_desc, _ = self._extract_oneforma_card_meta(desc)
                        loc_str = loc_from_desc

                    _, comp_raw = self._extract_oneforma_card_meta(desc)
                    if term_labels:
                        desc = f"{' '.join(term_labels)}\n{desc}"

                    if jid and title and slug:
                        by_slug[slug] = RawJobListing(
                            source_connector=self.source_id,
                            platform=self.platform,
                            source_job_id=jid,
                            title=title,
                            official_source_url=link,
                            raw_apply_url=link,
                            location_raw=loc_str,
                            description_text=desc,
                            compensation_raw=comp_raw,
                            official_posted_at=str(wp_item["date_gmt"]) + "Z" if wp_item.get("date_gmt") else None,
                            logo_url=self.logo,
                            pure_ai_eval_board=self.pure_ai_eval_board,
                        )
        except Exception:
            pass

        # 2. Fetch paginated SSR `/projects/` HTML pages to capture expanded country lists & pay cards
        # (and serve as full primary extractor if WP REST returned 404/empty)
        html_base = (self.config.get("html_endpoint") or self.official_url).rstrip("/") + "/"
        for page in range(1, self.MAX_HTML_PAGES + 1):
            page_url = html_base if page == 1 else urljoin(html_base, f"page/{page}/")
            try:
                resp_html = self.http.get(page_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
            except Exception:
                if page == 1 and not by_slug:
                    raise
                break

            soup = BeautifulSoup(resp_html.text, "html.parser")
            new_or_enriched = 0

            for a in soup.select("a[href*='/projects/'], a[href*='/job/'], a[href*='/jobs/']"):
                href = (a.get("href") or "").strip()
                full_url = urljoin("https://www.oneforma.com", href)
                slug = self._slug_from_url(full_url)
                if not slug:
                    continue

                container = a.find_parent(["article", "li"]) or a.find_parent("div") or a
                text = container.get_text(" ", strip=True)
                title_el = container.find(["h2", "h3", "h4"])
                title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
                if not title or title.lower() in ("apply", "apply now", "open projects", "view open projects", "next ›", "‹ previous"):
                    continue

                loc_card, comp_card = self._extract_oneforma_card_meta(text)
                if not loc_card:
                    loc_el = container.find(class_=re.compile(r"loc|country", re.I))
                    loc_card = loc_el.get_text(" ", strip=True) if loc_el else ""

                if slug in by_slug:
                    existing = by_slug[slug]
                    if loc_card and not existing.location_raw:
                        existing.location_raw = loc_card
                        new_or_enriched += 1
                    if comp_card and not existing.compensation_raw:
                        existing.compensation_raw = comp_card
                    if text and text not in existing.description_text:
                        existing.description_text = f"{existing.description_text}\n{text}"
                else:
                    new_or_enriched += 1
                    by_slug[slug] = RawJobListing(
                        source_connector=self.source_id,
                        platform=self.platform,
                        source_job_id=slug,
                        title=title,
                        official_source_url=full_url,
                        raw_apply_url=full_url,
                        location_raw=loc_card,
                        description_text=text,
                        compensation_raw=comp_card,
                        logo_url=self.logo,
                        pure_ai_eval_board=self.pure_ai_eval_board,
                    )

            has_next = bool(soup.find("a", href=re.compile(rf"/page/{page + 1}/?")))
            if not has_next or new_or_enriched == 0:
                break

        return list(by_slug.values())


class TelusAIConnector(BaseConnector):
    MAX_PAGES = 5

    def fetch_listings(self) -> List[RawJobListing]:
        listings: List[RawJobListing] = []
        seen: Set[str] = set()
        last_err: Optional[str] = None

        # Check primary endpoint (e.g. telusinternational.ai/opportunities) without anti-bot bypass
        for page in range(1, self.MAX_PAGES + 1):
            params = {"page": page} if page > 1 else None
            try:
                resp = self.http.get(
                    self.api_endpoint,
                    params=params,
                    timeout_sec=self.timeout_sec,
                    rate_limit_rps=self.rate_limit_rps,
                )
            except Exception as exc:
                last_err = str(exc)
                break

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

            for a in soup.select("a[href*='/jobs/available/'], a[href*='/job/'], a[href^='/jobs/'], a[href*='jobs.telusdigital.com/jobs/']"):
                href = (a.get("href") or "").strip()
                base_host = "https://www.telusinternational.ai" if "telusinternational.ai" in self.api_endpoint else "https://jobs.telusdigital.com"
                full_url = urljoin(base_host, href)
                container = a.find_parent(["li", "article", "div"]) or a
                text = container.get_text(" ", strip=True)
                m_req = re.search(r"(?:/jobs/available/(\d+)|\b(Req[A-Z0-9\-]+)\b)", f"{full_url} {text}")
                jid = (m_req.group(1) or m_req.group(2)) if m_req else full_url.rstrip("/").split("/")[-1]
                if jid in seen:
                    continue
                seen.add(jid)
                page_found += 1
                title_el = container.find(["h2", "h3", "h4"])
                title = title_el.get_text(" ", strip=True) if title_el else a.get_text(" ", strip=True)
                if not title or title.lower() == "view job":
                    continue
                loc_el = container.find(class_=re.compile(r"location|city|country", re.I))
                loc_str = loc_el.get_text(" ", strip=True) if loc_el else ""
                # TELUS SSR cards may pack title, location, language and req ID
                # into a single anchor. Keep only title in the title field.
                if "/jobs/" in full_url and re.search(r"\bReq[A-Z_-]*\d+", text):
                    req_match = re.search(r"\bReq[A-Z_-]*\d+", text)
                    before_req = text[:req_match.start()].strip() if req_match else text
                    before_meta = re.split(r"\s*[•·]\s*", before_req, maxsplit=1)[0].strip()
                    where = re.search(
                        r"\s+(Jakarta(?:\s+Selatan)?|Denpasar(?:\s+Barat)?|Surabaya|Bandung|Bali)"
                        r"\s*[,·•]\s*(?:[A-Z]{2}\s*[,·•]\s*)?Indonesia\b",
                        before_meta, re.IGNORECASE,
                    )
                    if where:
                        title = before_meta[:where.start()].strip()
                        loc_str = before_meta[where.start():].strip()
                    elif not title_el:
                        title = before_meta
                    if not loc_str and re.search(r"\bIndonesia\b", text, re.IGNORECASE):
                        loc_str = "Indonesia"
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

            if page_found == 0 and "enable javascript to run this app" in resp.text.lower():
                last_err = (
                    f"TELUS AI Community portal ({self.api_endpoint}) serves a client-side React SPA shell "
                    "requiring JS/geo-gated API; jobs.telusdigital.com enforces HTTP 403 access control"
                )
                break

            if page_found == 0 or not soup.select_one("a[rel='next'], a.next"):
                break

        if not listings and last_err:
            raise RuntimeError(last_err)
        return listings


class MindriftConnector(BaseConnector):
    WORKABLE_WIDGET_DEFAULT = "https://apply.workable.com/api/v1/widget/accounts/toloka-ai?details=true"

    def fetch_listings(self) -> List[RawJobListing]:
        by_id: Dict[str, RawJobListing] = {}
        headline_pay_by_code: Dict[str, str] = {}
        headline_pay_by_title: Dict[str, str] = {}
        html_cards: List[RawJobListing] = []

        # 1. Inspect mindrift.ai/apply HTML (for headline rates and standalone HTML fallback)
        html_url = self.config.get("html_endpoint") or self.official_url
        try:
            resp_html = self.http.get(html_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
            soup = BeautifulSoup(resp_html.text, "html.parser")
            seen_titles: Set[str] = set()

            for a in soup.select("a[href*='apply.workable.com'], [data-project-id], article, li.project-card, a[href*='/project/']"):
                container = a if a.name in ("article", "li") else (a.find_parent(["article", "li", "div"]) or a)
                text = container.get_text(" ", strip=True)
                title_el = container.find(["h2", "h3", "h4", "strong"])
                title = title_el.get_text(" ", strip=True) if title_el else ""
                if not title:
                    # Extract title preceding "up to $X /hr" on Webflow project rows
                    m_t = re.match(r"^(.{6,110}?)\s+(?:up\s+to\s+\$|Open\b)", text, re.IGNORECASE)
                    if m_t:
                        title = m_t.group(1).strip()
                if not title or title.lower() in ("apply", "see details", "register now"):
                    continue

                a_el = a if a.name == "a" else container.find("a", href=True)
                href = a_el["href"].strip() if a_el and a_el.get("href") else self.official_url
                full_url = urljoin("https://mindrift.ai", href)
                m_sc = re.search(r"/j/([A-Za-z0-9]+)", full_url)
                shortcode = m_sc.group(1) if m_sc else ""

                m_pay = re.search(r"(?:up\s+to\s+)?\$\s*\d+(?:\s*-\s*\$?\d+)?\s*/\s*(?:hr|hour)", text, re.IGNORECASE)
                comp_raw = re.sub(r"\$\s+", "$", m_pay.group(0)) if m_pay else ""
                if comp_raw:
                    if shortcode:
                        headline_pay_by_code[shortcode] = comp_raw
                    headline_pay_by_title[title.lower()] = comp_raw

                if title not in seen_titles:
                    seen_titles.add(title)
                    jid = shortcode or container.get("data-project-id") or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
                    html_cards.append(
                        RawJobListing(
                            source_connector=self.source_id,
                            platform=self.platform,
                            source_job_id=str(jid),
                            title=title,
                            official_source_url=full_url if shortcode else self.official_url,
                            raw_apply_url=full_url,
                            location_raw="",
                            description_text=text,
                            compensation_raw=comp_raw,
                            logo_url=self.logo,
                            pure_ai_eval_board=self.pure_ai_eval_board,
                        )
                    )
        except Exception:
            pass

        # 2. Query official Workable v1 public widget JSON (`toloka-ai`) and deduplicate by shortcode
        widget_url = self.api_endpoint if "workable.com" in self.api_endpoint else self.config.get("workable_api_endpoint", "")
        if widget_url:
            try:
                resp_w = self.http.get(widget_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
                payload = resp_w.json()
                jobs_arr = payload.get("jobs") if isinstance(payload, dict) else None
                if isinstance(jobs_arr, list):
                    grouped: Dict[str, Dict[str, Any]] = {}
                    for row in jobs_arr:
                        if not isinstance(row, dict):
                            continue
                        sc = str(row.get("shortcode") or row.get("code") or "").strip()
                        title = str(row.get("title") or "").strip()
                        if not sc or not title:
                            continue
                        entry = grouped.setdefault(
                            sc,
                            {
                                "row": row,
                                "countries": [],
                                "cities": [],
                                "telecommuting": bool(row.get("telecommuting")),
                            },
                        )
                        if row.get("telecommuting"):
                            entry["telecommuting"] = True

                        row_country = str(row.get("country") or "").strip()
                        row_city = str(row.get("city") or "").strip()
                        if row_country and row_country not in entry["countries"]:
                            entry["countries"].append(row_country)
                        if row_city and row_city not in entry["cities"]:
                            entry["cities"].append(row_city)

                        for loc_obj in row.get("locations") or []:
                            if not isinstance(loc_obj, dict) or loc_obj.get("hidden") is True:
                                continue
                            lc = str(loc_obj.get("country") or "").strip()
                            lcity = str(loc_obj.get("city") or "").strip()
                            if lc and lc not in entry["countries"]:
                                entry["countries"].append(lc)
                            if lcity and lcity not in entry["cities"]:
                                entry["cities"].append(lcity)

                    for sc, g in grouped.items():
                        row = g["row"]
                        title = str(row["title"]).strip()
                        countries: List[str] = g["countries"]
                        cities: List[str] = g["cities"]
                        is_remote = g["telecommuting"]

                        if countries:
                            # Ensure Indonesia stays visible if present in a long country list
                            ordered_countries = (
                                ["Indonesia"] + [c for c in countries if c.lower() != "indonesia"]
                                if any(c.lower() == "indonesia" for c in countries)
                                else countries
                            )
                            country_summary = ", ".join(ordered_countries[:30])
                            if is_remote:
                                loc_str = f"Remote - {country_summary}"
                            elif len(cities) == 1 and len(countries) == 1:
                                loc_str = f"{cities[0]}, {countries[0]}"
                            else:
                                loc_str = country_summary
                        else:
                            loc_str = "Remote" if is_remote else ""

                        apply_url = f"https://apply.workable.com/toloka-ai/j/{sc}"
                        desc_html = str(row.get("description") or title)
                        comp_raw = headline_pay_by_code.get(sc) or headline_pay_by_title.get(title.lower(), "")
                        posted_at = row.get("published_on") or row.get("created_at")

                        by_id[sc] = RawJobListing(
                            source_connector=self.source_id,
                            platform=self.platform,
                            source_job_id=sc,
                            title=title,
                            official_source_url=apply_url,
                            raw_apply_url=apply_url,
                            location_raw=loc_str,
                            description_text=desc_html,
                            compensation_raw=comp_raw,
                            official_posted_at=str(posted_at) if posted_at else None,
                            employment_type=str(row.get("employment_type") or ""),
                            department=str(row.get("department") or ""),
                            logo_url=self.logo,
                            pure_ai_eval_board=self.pure_ai_eval_board,
                        )
            except Exception:
                if not html_cards:
                    raise

        if by_id:
            return list(by_id.values())
        return html_cards


class IMeritConnector(BaseConnector):
    JOBS_JSON_DEFAULT = "https://imerit.ai/jobs.json"

    @staticmethod
    def _parse_closing_date(raw_val: Any) -> Optional[date]:
        if not raw_val:
            return None
        s = re.sub(r"^(?:closes|closing\s+date|expires)\s*:?\s*", "", str(raw_val).strip(), flags=re.IGNORECASE)
        if not s or s.lower() in ("none", "null", "n/a", "open", "ongoing"):
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
        return None

    @staticmethod
    def _is_specific_apply_url(url: str) -> bool:
        if not url.startswith(("https://", "http://")):
            return False
        parsed = urlparse(url)
        path = (parsed.path or "/").rstrip("/")
        if not path or path.lower() in ("/login", "/signup", "/sign-up", "/register", "/apply", "/careers", "/careers-listing"):
            return bool(parsed.query)
        return True

    def fetch_listings(self) -> List[RawJobListing]:
        json_url = self.api_endpoint if self.api_endpoint.endswith(".json") else self.config.get("jobs_json_endpoint", "")
        if json_url:
            try:
                json_listings = self._fetch_from_jobs_json(json_url)
                if json_listings:
                    return json_listings
            except Exception:
                if not self.config.get("html_endpoint"):
                    raise

        html_url = self.config.get("html_endpoint") or self.official_url
        resp = self.http.get(html_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        soup = BeautifulSoup(resp.text, "html.parser")
        listings: List[RawJobListing] = []
        seen: Set[str] = set()

        for card in soup.select("article, div.role-card, div.career-card, li.job-item, a[href*='?hash=']"):
            container = card if card.name in ("article", "div", "li") else (card.find_parent(["article", "div", "li"]) or card)
            title_el = container.find(["h2", "h3", "h4"])
            if not title_el:
                continue
            title = title_el.get_text(" ", strip=True)
            a_el = card if card.name == "a" else container.find("a", href=True)
            href = a_el["href"] if a_el and a_el.get("href") else self.official_url
            full_url = urljoin("https://imerit.ai", href)
            m_hash = re.search(r"[?&]hash=([A-Za-z0-9_\-]+)", full_url)
            jid = m_hash.group(1) if m_hash else full_url.rstrip("/").split("/")[-1]
            if not jid or jid == "careers-listing":
                jid = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
            if jid in seen:
                continue
            seen.add(jid)

            text = container.get_text(" ", strip=True)
            loc_el = container.find(class_=re.compile(r"loc", re.I))
            loc_str = loc_el.get_text(" ", strip=True) if loc_el else ""
            if not loc_str:
                m_pin = re.search(r"📍\s*([^⏱🕒🌐\n]+)", text)
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

    def _fetch_from_jobs_json(self, json_url: str) -> List[RawJobListing]:
        resp = self.http.get(json_url, timeout_sec=self.timeout_sec, rate_limit_rps=self.rate_limit_rps)
        payload = resp.json()
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = None
            for k in ("jobs", "roles", "data", "items", "openings", "positions"):
                if isinstance(payload.get(k), list):
                    items = payload[k]
                    break
            if items is None:
                raise ValueError("Unrecognized iMerit jobs.json structure")
        else:
            raise ValueError("Invalid iMerit jobs.json payload")

        today_utc = datetime.now(timezone.utc).date()
        listings: List[RawJobListing] = []
        seen: Set[str] = set()

        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(
                item.get("title")
                or item.get("role")
                or item.get("job_title")
                or item.get("jobTitle")
                or item.get("name")
                or ""
            ).strip()
            if not title:
                continue

            raw_hash = str(
                item.get("hash")
                or item.get("job_hash")
                or item.get("id")
                or item.get("job_id")
                or item.get("req_id")
                or ""
            ).strip()
            raw_link = str(
                item.get("apply_url")
                or item.get("application_url")
                or item.get("apply_link")
                or item.get("url")
                or item.get("link")
                or ""
            ).strip()
            if not raw_hash and raw_link:
                m_h = re.search(r"[?&]hash=([A-Za-z0-9_\-]+)", raw_link)
                if m_h:
                    raw_hash = m_h.group(1)

            jid = raw_hash or re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
            if jid in seen:
                continue
            seen.add(jid)

            source_url = (
                f"https://imerit.ai/careers-listing/?hash={raw_hash}"
                if raw_hash
                else (raw_link if self._is_specific_apply_url(raw_link) else self.official_url)
            )
            apply_url = raw_link if self._is_specific_apply_url(raw_link) else source_url

            loc_parts: List[str] = []
            for lk in ("location", "locations", "country", "countries", "city", "state"):
                lv = item.get(lk)
                if isinstance(lv, list):
                    loc_parts.extend(str(x).strip() for x in lv if x and str(x).strip())
                elif isinstance(lv, str) and lv.strip():
                    loc_parts.append(lv.strip())
            loc_str = ", ".join(dict.fromkeys(loc_parts))

            emp_str = str(
                item.get("type")
                or item.get("role_type")
                or item.get("employment_type")
                or item.get("job_type")
                or item.get("contract_type")
                or ""
            ).strip()
            dept_str = str(item.get("department") or item.get("category") or "").strip()
            lang_val = item.get("language") or item.get("languages") or ""
            lang_str = ", ".join(str(x) for x in lang_val if x) if isinstance(lang_val, list) else str(lang_val).strip()

            desc_chunks = [
                str(
                    item.get("overview")
                    or item.get("position_overview")
                    or item.get("positionOverview")
                    or item.get("description")
                    or item.get("job_description")
                    or item.get("summary")
                    or ""
                ).strip(),
                str(item.get("requirements") or item.get("qualifications") or "").strip(),
            ]
            if lang_str:
                desc_chunks.append(f"Language: {lang_str}")
            if emp_str:
                desc_chunks.append(f"Role Type: {emp_str}")
            desc_text = "\n".join(c for c in desc_chunks if c) or title

            raw_rate = str(
                item.get("rate")
                or item.get("pay")
                or item.get("hourly_rate")
                or item.get("pay_rate")
                or item.get("compensation")
                or item.get("salary")
                or ""
            ).strip()
            comp_raw = ""
            if raw_rate and raw_rate.lower() not in ("none", "null", "n/a", "tbd", "0", "0.0"):
                if re.fullmatch(r"\d+(?:\.\d+)?", raw_rate):
                    val_f = float(raw_rate)
                    if 0 < val_f <= 500:
                        comp_raw = f"${raw_rate}/hr"
                else:
                    comp_raw = raw_rate

            close_val = (
                item.get("closing_date")
                or item.get("closingDate")
                or item.get("closes")
                or item.get("close_date")
                or item.get("end_date")
                or item.get("expiry_date")
                or item.get("expires")
            )
            close_dt = self._parse_closing_date(close_val)
            status_low = str(item.get("status") or "").strip().lower()
            is_closed = (
                (close_dt is not None and close_dt < today_utc)
                or status_low in ("closed", "expired", "inactive", "filled")
                or item.get("is_active") is False
            )

            posted_at = (
                item.get("posted_date")
                or item.get("postedDate")
                or item.get("date_posted")
                or item.get("created_at")
                or item.get("published_at")
            )

            listings.append(
                RawJobListing(
                    source_connector=self.source_id,
                    platform=self.platform,
                    source_job_id=jid,
                    title=title,
                    official_source_url=source_url,
                    raw_apply_url=apply_url,
                    location_raw=loc_str,
                    description_text=desc_text,
                    compensation_raw=comp_raw,
                    official_posted_at=str(posted_at) if posted_at else None,
                    employment_type=emp_str,
                    department=dept_str,
                    logo_url=self.logo,
                    pure_ai_eval_board=self.pure_ai_eval_board,
                    is_explicitly_closed=is_closed,
                )
            )

        return listings
