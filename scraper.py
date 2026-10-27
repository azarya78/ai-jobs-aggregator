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

def clean_referral_url(url):
    if not url:
        return ""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    clean_params = {
        k: v for k, v in query_params.items()
        if not k.lower().startswith("utm_") and k.lower() not in ["ref", "source", "ch"]
    }
    new_query = urlencode(clean_params, doseq=True)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))

def resolve_apply_url(remowork_apply_path, fallback_url):
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
    """Mengubah teks tanggal (misal 'Oct 27, 2026') menjadi angka agar bisa diurutkan dari yang paling baru"""
    if not date_str:
        return 0
    clean_str = date_str.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(clean_str, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            pass
    return 0

def scrape_remowork_pages():
    # Mengambil lowongan terbaru (sort=recent / default terbaru) + halaman khusus Indonesia agar tab Indonesia tetap lengkap
    urls_to_fetch = [
        ("https://remowork.life/jobs", "Global AI"),
        ("https://remowork.life/jobs?page=2", "Global AI"),
        ("https://remowork.life/jobs?language=indonesian", "Indonesian")
    ]
    jobs = []
    seen_ids = set()

    for page_url, group_tag in urls_to_fetch:
        print(f"Mengambil dari RemoWork: {page_url}")
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

                jobs.append({
                    "id": job_id,
                    "title": title,
                    "company": company,
                    "logo": logo,
                    "category": category,
                    "eligible": eligible,
                    "isIndonesian": is_indonesian,
                    "skills": skills[:8],
                    "location": location,
                    "pay": pay,
                    "date": posted_date,
                    "timestamp": parse_job_date(posted_date),
                    "excerpt": excerpt,
                    "applyUrl": direct_url,
                    "source": "RemoWork"
                })
        except Exception as e:
            print(f"Error saat mengambil {page_url}: {e}")
        time.sleep(1)

    return jobs

def fetch_direct_lever_jobs():
    lever_companies = [
        ("welocalize", "Welocalize", "https://www.google.com/s2/favicons?domain=welocalize.com&sz=128")
    ]
    direct_jobs = []
    keywords = ["ai", "rater", "annotat", "evaluat", "indonesia", "grader", "data", "search", "linguist", "freelance"]

    for slug, company_name, logo_url in lever_companies:
        api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        print(f"Mengambil langsung dari API resmi {company_name}...")
        try:
            resp = requests.get(api_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                continue
            postings = resp.json()
            for post in postings:
                title = post.get("text", "")
                categories = post.get("categories", {})
                location = categories.get("location", "Remote")
                team = categories.get("team", "AI / Data")
                commitment = categories.get("commitment", "Freelance")
                desc_plain = post.get("descriptionPlain", "")[:220] + "..."
                apply_url = post.get("hostedUrl", "")

                combined_text = f"{title} {location} {team}".lower()
                if not any(k in combined_text for k in keywords):
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
                    "category": f"{team} ({commitment})",
                    "eligible": True,
                    "isIndonesian": is_indo,
                    "skills": [team, commitment, "Direct Source"],
                    "location": location,
                    "pay": "Sesuai Proyek",
                    "date": date_str,
                    "timestamp": ts,
                    "excerpt": desc_plain.replace("\n", " "),
                    "applyUrl": clean_referral_url(apply_url),
                    "source": "Direct API"
                })
        except Exception as e:
            print(f"Error Lever API {company_name}: {e}")

    return direct_jobs

def main():
    all_jobs = []
    all_jobs.extend(scrape_remowork_pages())
    all_jobs.extend(fetch_direct_lever_jobs())

    # URUTKAN MURNI BERDASARKAN TANGGAL TERBARU (Recent Projects paling atas!)
    all_jobs.sort(key=lambda x: x.get("timestamp", 0), reverse=True)

    output = {
        "lastUpdated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "totalJobs": len(all_jobs),
        "jobs": all_jobs
    }

    with open("jobs_feed.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Berhasil menyimpan {len(all_jobs)} lowongan (Urutan Terbaru) ke jobs_feed.json!")

if __name__ == "__main__":
    main()
