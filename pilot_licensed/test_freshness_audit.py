from freshness_audit import audit,fetch_opentrain
NOW="2026-11-26T00:00:00Z"
def test_soji_date_not_official():
    row={"id":"1","title":"AI Trainer","platform":"Micro1","platformUrl":"https://jobs.micro1.ai/post/a","summary":"Evaluate AI model responses","postedAt":NOW}
    report=audit([row],[],NOW,"SOJI")
    assert report["counts"]["feed_date_7d"]==1
    assert report["official_dates_verified"]==0
def test_overlap_upstream():
    row={"id":"1","title":"AI Trainer","platform":"Micro1","platformUrl":"https://jobs.micro1.ai/post/a","summary":"Evaluate AI model responses","postedAt":NOW}
    report=audit([row],[{"upstreamApplyUrl":row["platformUrl"]}],NOW,"SOJI")
    assert report["counts"]["duplicate_url"]==1
