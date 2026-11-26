from ai_jobs_v2.project_groups import build_project_groups

NOW="2026-11-26T12:00:00Z"
def item(i, **kw):
    return dict(id=i, title="AI Trainer", company="Example", sourceConnector="a",
                projectContentHash="same", fullDescriptionHash="lifecycle-"+i, excerpt="Task", applyUrl="https://example.com/"+i,
                officialPostedDate=NOW, **kw)

def test_group_keeps_all_links_and_regions():
    items=[item("1",location="Canada"),item("2",location="Indonesia")]
    result=build_project_groups(items,NOW)
    assert result["totalContentGroups"]==1
    assert result["groups"][0]["listingIds"]==["1","2"]
    assert items[0]["applyUrl"]!=items[1]["applyUrl"]
    assert items[0]["projectGroupId"]==items[1]["projectGroupId"]

def test_different_sources_or_description_do_not_merge():
    items=[item("1"),item("2"),item("3")]
    items[1]["sourceConnector"]="b"
    items[2]["projectContentHash"]="other"
    assert build_project_groups(items,NOW)["totalContentGroups"]==3

def test_missing_hash_not_grouped():
    items=[item("1"),item("2")]
    for x in items: x["projectContentHash"]=""
    assert build_project_groups(items,NOW)["totalContentGroups"]==2

def test_recent_group_count_and_future_date():
    items=[item("1"),item("2"),item("3")]
    items[2]["title"]="Another"
    items[2]["officialPostedDate"]="2026-12-01T00:00:00Z"
    result=build_project_groups(items,NOW)
    assert result["contentGroupsReported7d"]==1


def test_normalized_content_hash_ignores_location_without_changing_lifecycle():
    from ai_jobs_v2.models import RawJobListing
    from ai_jobs_v2.normalizer import normalize_raw_listing
    def raw(i, loc, desc):
        return RawJobListing("test", "Example", i, "AI Trainer", "https://example.com/jobs/"+i,
                             "https://example.com/jobs/"+i, location_raw=loc, description_text=desc)
    a, _, _ = normalize_raw_listing(raw("1", "Canada", "<p>Evaluate LLM responses.</p>"), NOW)
    b, _, _ = normalize_raw_listing(raw("2", "Indonesia", "Evaluate   LLM responses."), NOW)
    assert a.project_content_hash == b.project_content_hash
    assert a.full_description_hash != b.full_description_hash
    assert build_project_groups([a.to_android_feed_item(), b.to_android_feed_item()], NOW)["totalContentGroups"] == 1
