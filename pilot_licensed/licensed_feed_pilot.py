"""Isolated licensed feed inspection; no publication or raw descriptions persisted."""
import json
from collections import Counter
from pathlib import Path
from ai_jobs_v2.http_client import PoliteHTTPClient

SOURCES = [
 ("AITrainer.work", "https://aitrainer.work/data/jobs.json", "https://aitrainer.work/job-feed", "CC BY 4.0; preserve feed landing link and attribution"),
 ("OpenTrain AI", "https://app.opentrain.ai/api/public/v1/jobs", "https://www.opentrain.ai/jobs/feed/", "Public listing reuse; retain apply link and via OpenTrain"),
 ("SOJI", "https://hiring.sojistudio.com/feed/jobs.json", "https://hiring.sojistudio.com/open-data", "CC BY 4.0; attribution required"),
]

def rows_from(payload):
    if isinstance(payload, list):
        return payload, "array"
    if isinstance(payload, dict):
        for k in ("jobs", "items", "results", "data"):
            value = payload.get(k)
            if isinstance(value, list):
                return value, k
            if isinstance(value, dict):
                try:
                    rows, path = rows_from(value)
                    return rows, k + "." + path
                except ValueError:
                    pass
    raise ValueError("Unrecognized listing container")

def inspect(payload):
    rows, path = rows_from(payload)
    objects = [row for row in rows if isinstance(row, dict)]
    fields = Counter(k for row in objects for k, v in row.items() if v is not None and v != "")
    platforms = Counter()
    for row in objects:
        for key in ("platform", "company", "company_name", "employer", "source"):
            value = row.get(key)
            if isinstance(value, str) and value.strip():
                platforms[value.strip()] += 1
                break
    return dict(container=path, rows=len(rows), object_rows=len(objects),
                populated_fields=dict(fields), platform_counts=dict(platforms),
                top_level_fields=list(payload) if isinstance(payload, dict) else [],
                pagination_metadata={k:v for k,v in payload.items() if k in ("total","count","page","limit","has_more","hasMore") and isinstance(v,(int,bool))} if isinstance(payload,dict) else {},
                limitations="Counts reflect returned page only; dates and upstream identity not validated.")

def main():
    client = PoliteHTTPClient(max_retries=0)
    reports = []
    for name, endpoint, documentation, reuse in SOURCES:
        row = dict(name=name, endpoint=endpoint, documentation=documentation, reuse=reuse)
        try:
            response = client.get(endpoint, timeout_sec=25, rate_limit_rps=1)
            row.update(http_status=response.status_code, **inspect(response.json()), status="SUCCESS")
        except Exception as exc:
            row.update(status="FAILED", error=str(exc)[:300])
        reports.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    Path("licensed-feed-pilot-report.json").write_text(json.dumps(dict(experimentalOnly=True, doNotPublishToAndroid=True, sources=reports), indent=2))

if __name__ == "__main__":
    main()
