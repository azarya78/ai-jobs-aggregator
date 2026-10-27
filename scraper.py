import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8"
}

AI_KEYWORDS = [
    "ai", "rater", "annotat", "evaluat", "indonesia", "grader", "data",
    "search", "linguist", "freelance", "tutor", "trainer", "translator",
    "prompt", "rlhf", "model", "label", "content", "specialist", "bilingual"
]

def clean_referral_url(url):
    """Membuang parameter referral (?utm_source=remowork, dll.) dari URL"""
    if not url:
        return ""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    clean_params = {
        k: v for k, v in query_params.items()
        if not k.lower().startswith("utm_") and k.lower() not in ["ref", "source", "ch", "gh_src"]
    }
    new_query = urlencode(clean_params, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def resolve_apply_url(remowork_apply_path, fallback_url):
    """Membongkar link /apply-out untuk mendapatkan URL asli perusahaan"""
    if not remowork_apply_path:
        return fallback_url
    full_url = f"https://remowork.life{remowork_apply_path}" if remowork_apply_path.startswith("/") else remowork_apply_path
    try:
        resp = requests.head(full_url, headers=HEADERS, allow_redirects=True, timeout=8)
        if resp.url and "remowork.life" not in resp.url:
            return clean_referral_url(resp.url)
    except Exception:
        pass
    return full_url

def parse_job_date(date_str):
    """Mengubah teks tanggal (misal 'Oct 27, 2026') menjadi timestamp agar bisa diurutkan"""
    if not date_str:
        return 0
    clean_str = date_str.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(clean_str, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return 0

def parse_iso_date(iso_str):
    """Mengubah format tanggal ISO dari Direct API menjadi timestamp dan teks tanggal rapi"""
    if not iso_str:
        return 0, "Baru"
    try:
        clean_iso = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_iso)
        return int(dt.timestamp()), dt.strftime("%b %d, %Y")
    except Exception:
        return 0, "Baru"

def scrape_remowork_pages():
    urls_to_fetch = [
        ("https://remowork.life/jobs", "Global AI"),
        ("https://remowork.life/jobs?page=2", "Global AI"),
        ("https://remowork.life/jobs?page=3", "Global AI"),
        ("https://remowork.life/jobs?language=indonesian", "Indonesian")
    ]
    jobs = []
    seen_ids = set()

    for page_url, group_tag in urls_to_fetch:
        print(f"Mengambil halaman: {page_url}")
        try:
            resp = requests.get(page_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.select("article.jb-card, div.jb-card")

            for card in cards:
                title_el = card.select_one(".jb-card-title")
                title = title_el.get_text(strip=True) if title_el else "Untitled Job"

                link_el = card.select_one(".jb-card-title a") or card.select_one(".jb-card-actions a")
                detail_path = link_el.get("href", "") if link_el else ""
                job_id = detail_path.split("/jobs/")[-1].split("/")[0] if "/jobs/" in detail_path else f"{title}"

                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                company_el = card.select_one(".jb-card-company")
                company = company_el.get_text(strip=True) if company_el else "Unknown Company"

                logo_el = card.select_one(".jb-card-logo img")
                logo = logo_el.get("src", "") if logo_el else ""

                cat_el = card.select_one(".jb-card-tag--category")
                category = cat_el.get_text(strip=True) if cat_el else group_tag

                eligible = bool(card.select_one(".jb-card-tag--eligible"))
                skills = [s.get_text(strip=True) for s in card.select(".jb-card-tag--skill, .jb-card-tag-popover-item")]

                meta_items = [li.get_text(" ", strip=True) for li in card.select(".jb-card-meta li")]
                location = meta_items[0] if len(meta_items) > 0 else "Remote (Global)"
                pay = "Lihat Detail"
                posted_date = ""
                for item in meta_items:
                    if "$" in item or "/hr" in item.lower() or "usd" in item.lower():
                        pay = item
                    elif any(m in item for m in ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]):
                        posted_date = item

                excerpt_el = card.select_one(".jb-card-excerpt")
                excerpt = excerpt_el.get_text(strip=True) if excerpt_el else ""

                apply_el = card.select_one("a.jb-card-btn--primary")
                apply_path = apply_el.get("href", "") if apply_el else detail_path
                detail_url = f"https://remowork.life{detail_path}" if detail_path.startswith("/") else detail_path

                direct_url = resolve_apply_url(apply_path, detail_url) if len(jobs) < 25 else (
                    f"https://remowork.life{apply_path}" if apply_path.startswith("/") else detail_url
                )

                is_indonesian = (
                    group_tag == "Indonesian"
                    or "indonesia" in title.lower()
                    or "indonesian" in " ".join(skills).lower()
                    or "indonesia" in location.lower()
                )

                skill_badge = " • ".join(skills[:2]) if skills else ("✓ Open to you" if eligible else "Global Remote")

                jobs.append({
                    "id": job_id,
                    "title": title,
                    "company": company,
                    "logo": logo,
                    "category": category,
                    "eligible": eligible,
                    "isIndonesian": is_indonesian,
                    "isDirect": False,
                    "skills": skills[:8],
                    "location": location,
                    "pay": pay,
                    "date": posted_date,
                    "timestamp": parse_job_date(posted_date),
                    "excerpt": excerpt,
                    "applyUrl": direct_url,
                    "source": skill_badge
                })
        except Exception as e:
            print(f"Error saat mengambil {page_url}: {e}")
        time.sleep(1)

    return jobs

def fetch_direct_lever_jobs():
    """1. Konektor API Resmi Lever (Welocalize, dll.)"""
    lever_companies = [
        ("welocalize", "Welocalize", "https://www.google.com/s2/favicons?domain=welocalize.com&sz=128")
    ]
    direct_jobs = []

    for slug, company_name, logo_url in lever_companies:
        api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        print(f"Mengambil dari Lever API: {company_name}...")
        try:
            resp = requests.get(api_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            for post in resp.json():
                title = post.get("text", "")
                categories = post.get("categories", {})
                location = categories.get("location", "Remote")
                team = categories.get("team", "AI / Data")
                commitment = categories.get("commitment", "Freelance")
                desc_plain = post.get("descriptionPlain", "")[:220] + "..."
                apply_url = post.get("hostedUrl", "")

                combined_text = f"{title} {location} {team}".lower()
                if not any(k in combined_text for k in AI_KEYWORDS):
                    continue

                is_indo = "indonesia" in combined_text
                created_at = post.get("createdAt", 0)
                ts = int(created_at / 1000) if created_at else 0
                date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%b %d, %Y") if ts else "Baru"

                direct_jobs.append({
                    "id": f"lever-{post.get('id', title)}",
                    "title": title,
                    "company": company_name,
                    "logo": logo_url,
                    "category": team,
                    "eligible": True,
                    "isIndonesian": is_indo,
                    "isDirect": True,
                    "skills": [team, commitment, "Direct API"],
                    "location": location,
                    "pay": "Sesuai Proyek",
                    "date": date_str,
                    "timestamp": ts,
                    "excerpt": desc_plain.replace("\n", " "),
                    "applyUrl": clean_referral_url(apply_url),
                    "source": f"⚡ {commitment}"
                })
        except Exception as e:
            print(f"Error Lever API {company_name}: {e}")

    return direct_jobs

def fetch_direct_greenhouse_jobs():
    """2. Konektor API Resmi Greenhouse (Scale AI, xAI, Cohere, HumanSignal)"""
    gh_companies = [
        ("scaleai", "Scale AI", "https://www.google.com/s2/favicons?domain=scale.com&sz=128"),
        ("xai", "xAI", "https://www.google.com/s2/favicons?domain=x.ai&sz=128"),
        ("cohere", "Cohere", "https://www.google.com/s2/favicons?domain=cohere.com&sz=128"),
        ("humansignal", "HumanSignal", "https://www.google.com/s2/favicons?domain=humansignal.com&sz=128")
    ]
    direct_jobs = []

    for slug, company_name, logo_url in gh_companies:
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
        print(f"Mengambil dari Greenhouse API: {company_name}...")
        try:
            resp = requests.get(api_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            postings = resp.json().get("jobs", [])
            for post in postings:
                title = post.get("title", "")
                location = post.get("location", {}).get("name", "Remote")
                updated_at = post.get("updated_at", "")
                apply_url = post.get("absolute_url", "")

                combined_text = f"{title} {location}".lower()
                if not any(k in combined_text for k in AI_KEYWORDS):
                    continue

                is_indo = "indonesia" in combined_text
                ts, date_str = parse_iso_date(updated_at)

                raw_html = post.get("content", "")
                clean_excerpt = (
                    BeautifulSoup(BeautifulSoup(raw_html, "html.parser").get_text(" "), "html.parser")
                    .get_text(" ", strip=True)[:220] + "..."
                    if raw_html else f"Lowongan resmi langsung dari portal karir {company_name}."
                )

                direct_jobs.append({
                    "id": f"gh-{post.get('id', title)}",
                    "title": title,
                    "company": company_name,
                    "logo": logo_url,
                    "category": "AI & Data",
                    "eligible": True,
                    "isIndonesian": is_indo,
                    "isDirect": True,
                    "skills": ["Direct API", company_name],
                    "location": location,
                    "pay": "Lihat Detail",
                    "date": date_str,
                    "timestamp": ts,
                    "excerpt": clean_excerpt,
                    "applyUrl": clean_referral_url(apply_url),
                    "source": "⚡ Direct API"
                })
        except Exception as e:
            print(f"Error Greenhouse API {company_name}: {e}")

    return direct_jobs

def fetch_direct_ashby_jobs():
    """3. Konektor API Resmi AshbyHQ (Cohere, Roboflow, ElevenLabs)"""
    ashby_companies = [
        ("cohere", "Cohere", "https://www.google.com/s2/favicons?domain=cohere.com&sz=128"),
        ("roboflow", "Roboflow", "https://www.google.com/s2/favicons?domain=roboflow.com&sz=128"),
        ("elevenlabs", "ElevenLabs", "https://www.google.com/s2/favicons?domain=elevenlabs.io&sz=128")
    ]
    direct_jobs = []

    for slug, company_name, logo_url in ashby_companies:
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        print(f"Mengambil dari Ashby API: {company_name}...")
        try:
            resp = requests.get(api_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            postings = resp.json().get("jobs", [])
            for post in postings:
                title = post.get("title", "")
                location = post.get("location", "Remote")
                department = post.get("department", "AI Project")
                published_at = post.get("publishedAt", "")
                apply_url = post.get("jobUrl", "") or post.get("applyUrl", "")

                combined_text = f"{title} {location} {department}".lower()
                if not any(k in combined_text for k in AI_KEYWORDS):
                    continue

                is_indo = "indonesia" in combined_text
                ts, date_str = parse_iso_date(published_at)
                desc_plain = (
                    post.get("descriptionPlain", "")[:220] + "..."
                    if post.get("descriptionPlain") else f"Proyek langsung dari portal resmi {company_name}."
                )

                direct_jobs.append({
                    "id": f"ashby-{post.get('id', title)}",
                    "title": title,
                    "company": company_name,
                    "logo": logo_url,
                    "category": department,
                    "eligible": True,
                    "isIndonesian": is_indo,
                    "isDirect": True,
                    "skills": [department, "Direct API"],
                    "location": location,
                    "pay": "Lihat Detail",
                    "date": date_str,
                    "timestamp": ts,
                    "excerpt": desc_plain.replace("\n", " "),
                    "applyUrl": clean_referral_url(apply_url),
                    "source": "⚡ Direct API"
                })
        except Exception as e:
            print(f"Error Ashby API {company_name}: {e}")

    return direct_jobs

def deduplicate_jobs(jobs_list):
    """Menggabungkan lowongan kembar (Judul + Perusahaan sama) dari berbagai negara/sumber menjadi 1 kartu"""
    grouped = {}
    for job in jobs_list:
        norm_title = job.get("title", "").strip().lower()
        norm_comp = job.get("company", "").strip().lower()
        key = f"{norm_comp}||{norm_title}"

        if key not in grouped:
            job["_locations"] = [job.get("location", "Remote")]
            grouped[key] = job
        else:
            existing = grouped[key]
            loc = job.get("location", "")
            if loc and loc not in existing["_locations"]:
                existing["_locations"].append(loc)

            if job.get("isIndonesian"):
                existing["isIndonesian"] = True

            if existing.get("pay") in ["Lihat Detail", "Sesuai Proyek"] and "$" in job.get("pay", ""):
                existing["pay"] = job["pay"]

            if job.get("isDirect") and not existing.get("isDirect"):
                existing["applyUrl"] = job["applyUrl"]
                existing["isDirect"] = True

            if job.get("timestamp", 0) > existing.get("timestamp", 0):
                existing["timestamp"] = job["timestamp"]
                existing["date"] = job["date"]

    final_jobs = []
    for key, job in grouped.items():
        locs = job.pop("_locations", [])
        if len(locs) > 1:
            indo_locs = [l for l in locs if "indonesia" in l.lower() or "global" in l.lower()]
            first_loc = indo_locs[0] if indo_locs else locs[0]
            job["location"] = f"{first_loc} (+{len(locs)-1} lokasi lain)"
        final_jobs.append(job)

    return final_jobs

def main():
    all_jobs = []
    all_jobs.extend(scrape_remowork_pages())
    all_jobs.extend(fetch_direct_lever_jobs())
    all_jobs.extend(fetch_direct_greenhouse_jobs())
    all_jobs.extend(fetch_direct_ashby_jobs())

    # Gabungkan proyek kembar beda negara agar tidak double di layar
    unique_jobs = deduplicate_jobs(all_jobs)

    # Urutkan dari yang paling baru rilis (Recent Projects)
    unique_jobs.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

    output = {
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "totalJobs": len(unique_jobs),
        "jobs": unique_jobs
    }

    with open("jobs_feed.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Berhasil menyaring {len(all_jobs)} lowongan menjadi {len(unique_jobs)} proyek unik ke jobs_feed.json!")

if __name__ == "__main__":
    main()
