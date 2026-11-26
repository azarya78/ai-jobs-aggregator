import pytest
from aitrainer_live import fetch_pages,summarize
class Response:
    def __init__(self,data): self.data=data
    def json(self): return self.data
class Client:
    def __init__(self,pages): self.pages=pages
    def get(self,url,params,**kwargs): return Response(self.pages[params["offset"]])
def test_pagination():
    rows,pages=fetch_pages(Client({0:{"jobs":[{"url":"https://aitrainer.work/jobs/a"}],"total":2,"next_offset":1},1:{"jobs":[{"url":"https://aitrainer.work/jobs/b"}],"total":2,"next_offset":None}}))
    assert pages==2
    assert summarize(rows,pages)["invalid_attribution_links"]==0
def test_incomplete():
    with pytest.raises(ValueError): fetch_pages(Client({0:{"jobs":[],"total":3,"next_offset":None}}))
def test_wrong_host():
    assert summarize([{"url":"https://example.com/jobs/a"}],1)["invalid_attribution_links"]==1

def test_identical_rows_collapse():
    from aitrainer_live import partition_landing_rows
    row={"url":"https://aitrainer.work/jobs/a","id":"1"}
    safe,report=partition_landing_rows([row,dict(row)])
    assert safe==[row]
    assert report["exact_duplicate_rows"]==1
def test_conflicting_landing_quarantined():
    from aitrainer_live import partition_landing_rows
    a={"url":"https://aitrainer.work/jobs/a","id":"1"}
    b={"url":"https://aitrainer.work/jobs/a","id":"2"}
    c={"url":"https://aitrainer.work/jobs/b","id":"3"}
    safe,report=partition_landing_rows([a,b,c])
    assert safe==[c]
    assert report["quarantined_conflict_rows"]==2
    assert report["conflict_groups"]==1
def test_bad_authority_quarantined():
    from aitrainer_live import partition_landing_rows
    safe,report=partition_landing_rows([{"url":"https://user@aitrainer.work/jobs/a"}])
    assert not safe
    assert report["invalid_rows"]==1
