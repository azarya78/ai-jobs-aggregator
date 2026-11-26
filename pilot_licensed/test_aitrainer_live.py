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
