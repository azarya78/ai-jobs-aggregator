from ai_jobs_v2.licensed_opentrain import make_item, merge_snapshot, valid_link
NOW="2026-11-26T00:00:00Z"
ROW={"id":"1","title":"AI Trainer","companyName":"OpenTrain AI","descriptionText":"Evaluate AI model responses using rubrics","status":"OPEN","url":"https://app.opentrain.ai/jobs/a","applyUrl":"https://app.opentrain.ai/jobs/a/apply?utm_source=hub","datePosted":"2026-11-25T00:00:00Z","validThrough":"2026-12-01T00:00:00Z","applyMode":"signup_first"}
def test_open_attributed_preserve_link():
    item=make_item(ROW,NOW)
    assert item and item["applyUrl"]==ROW["applyUrl"]
    assert item["officialPostedDate"] is None and item["date"]==""
    assert item["marketplacePostedDate"] and "Via OpenTrain" in item["excerpt"]
def test_closed_and_expired():
    assert make_item(dict(ROW,status="ONGOING"),NOW) is None
    assert make_item(dict(ROW,validThrough="2026-11-20T00:00:00Z"),NOW) is None
def test_invalid_link():
    assert not valid_link("https://app.opentrain.ai.evil.example/a")
    assert not valid_link("https://user@app.opentrain.ai/a")
def test_merge_overlap():
    item=make_item(ROW,NOW)
    added,report=merge_snapshot([{"id":"x","applyUrl":ROW["applyUrl"]}],{"status":"SUCCESS","checkedAt":NOW,"items":[item]},NOW)
    assert not added and report["duplicates"]==1

def test_public_landing_and_original_apply():
    item=make_item(dict(ROW,url="https://www.opentrain.ai/jobs/a"),NOW)
    assert item and item["officialSourceUrl"]=="https://www.opentrain.ai/jobs/a"
    assert item["applyUrl"]==ROW["applyUrl"]
    assert not valid_link("https://www.opentrain.ai/jobs/a")
    assert not valid_link("https://www.opentrain.ai.evil.example/jobs/a",landing=True)
