"""Official sitemap URL collection must be bounded and host constrained."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from probe_official_sitemaps import locs_from_xml,inspect

class Response:
    def __init__(self,status,text="",url="https://app.begun.com/sitemap.xml"):
        self.status_code=status
        self.text=text
        self.url=url
        self.content=text.encode()

class FakeSession:
    def __init__(self,pages):
        self.pages=pages
    def get(self,url,**kwargs):
        return self.pages[url]

SOURCE={"id":"begun_official_sitemap","platform":"Begun","root":"https://app.begun.com",
        "sitemap":"https://app.begun.com/sitemap.xml","jobPathPrefix":"/opportunities/"}
LIMITS={"maxSitemapDocuments":3,"maxUrlsPerDocument":100,"timeoutSeconds":2}

def test_urlset_parser():
    typ,urls=locs_from_xml('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://app.begun.com/opportunities/analyst</loc></url></urlset>')
    assert typ=="urlset"
    assert len(urls)==1

def test_host_and_prefix_guard():
    xml="""<urlset><url><loc>https://app.begun.com/opportunities/analyst</loc></url>
      <url><loc>https://evil.example/opportunities/hijack</loc></url>
      <url><loc>https://app.begun.com/opportunities/analyst?ref=x</loc></url>
      <url><loc>https://app.begun.com/opportunity</loc></url></urlset>"""
    pages={"https://app.begun.com/robots.txt":Response(404),
           "https://app.begun.com/sitemap.xml":Response(200,xml)}
    out=inspect(FakeSession(pages),SOURCE,LIMITS)
    assert out["officialJobUrlCount"]==1
    assert out["sampleOfficialUrls"]==["https://app.begun.com/opportunities/analyst"]
    assert not out["reusePermissionVerified"]

def test_robots_disallow():
    pages={"https://app.begun.com/robots.txt":Response(200,"User-agent: *\nDisallow: /sitemap.xml")}
    out=inspect(FakeSession(pages),SOURCE,LIMITS)
    assert out["state"]=="ROBOTS_NOT_CLEARED"
    assert out["sitemapsFetched"]==0
