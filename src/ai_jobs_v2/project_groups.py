"""Conservative content groups for presentation, not verified project identities."""
import hashlib
import json
from collections import defaultdict
from .freshness_metrics import parse_date, hours_since

def build_project_groups(items, now_iso):
    now = parse_date(now_iso)
    buckets = defaultdict(list)
    for item in items:
        title = " ".join((item.get("title") or "").casefold().split())
        digest = item.get("projectContentHash")
        source = item.get("sourceConnector")
        company = item.get("company")
        # No fuzzy title matching, cross-platform matching, or missing evidence.
        key = ("content", source, company, title, digest) if all((source, company, title, digest, item.get("excerpt"))) else ("single", item["id"])
        buckets[key].append(item)
    groups = []
    for key, members in buckets.items():
        gid = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:24]
        dates = [x.get("officialPostedDate") for x in members]
        valid = [x for x in dates if hours_since(x, now) is not None]
        newest = max(valid, key=parse_date) if valid else None
        ids = [x["id"] for x in members]
        group = dict(groupId=gid, matchBasis="exact_title_description_same_source" if len(members)>1 else "singleton",
                     title=members[0]["title"], company=members[0]["company"],
                     listingIds=ids, listingCount=len(ids), latestReportedPostingDate=newest)
        groups.append(group)
        for item in members:
            item["projectGroupId"] = gid
            item["projectGroupSize"] = len(ids)
    groups.sort(key=lambda g: (g["latestReportedPostingDate"] is not None,
                               parse_date(g["latestReportedPostingDate"]).timestamp() if g["latestReportedPostingDate"] else 0,
                               g["groupId"]), reverse=True)
    recent = [g for g in groups if (age := hours_since(g["latestReportedPostingDate"],now)) is not None and age<=168]
    return dict(totalContentGroups=len(groups), totalListings=len(items),
                multiListingGroups=sum(g["listingCount"]>1 for g in groups),
                contentGroupsReported7d=len(recent),
                limitations=["Content similarity is not confirmed project identity.",
                             "Counts use existing catalog representatives, not upstream listings already deduplicated.",
                             "All catalog listing IDs and individual Apply links remain available."], groups=groups)
