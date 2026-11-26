from ai_jobs_v2.licensed_aitrainer import make_item, merge_snapshot, refresh_snapshot
NOW="2026-11-26T00:00:00Z"
ROW={"title":"AI Trainer","company":"Micro1","url":"https://aitrainer.work/jobs/a","apply_url":"https://jobs.micro1.ai/post/a","description":"Evaluate AI model responses using rubrics","first_seen":"2026-11-25","pay_text":"$20/hr"}
def test_syndicated_date_attribution_and_eligibility():
    item=make_item(ROW,NOW)
    assert item and item["date"]=="" and item["officialPostedDate"] is None
    assert item["isDirect"] is False and item["eligibilityClass"]=="Unverified"
    assert item["applyUrl"]==ROW["url"] and "AITrainer.work" in item["excerpt"]
    assert item["pay"]=="$20/hr (feed-reported; verify)"
def test_direct_overlap():
    item=make_item(ROW,NOW)
    snap={"items":[item],"status":"SUCCESS","checkedAt":NOW}
    added,report=merge_snapshot([{"id":"direct","applyUrl":ROW["apply_url"]+"?utm_source=x"}],snap)
    assert not added and report["crossSourceOrInternalDuplicates"]==1
def test_cache_throttle(tmp_path):
    import json
    path=tmp_path/"cache.json"
    path.write_text(json.dumps({"checkedAt":NOW,"status":"SUCCESS","items":[]}))
    assert refresh_snapshot(None,path,"2026-11-26T04:00:00Z")["status"]=="SUCCESS"
def test_failed_refresh_drops_stale(tmp_path):
    path=tmp_path/"cache.json"
    result=refresh_snapshot(None,path,NOW)
    assert result["status"]=="FAILED" and not result["items"]
