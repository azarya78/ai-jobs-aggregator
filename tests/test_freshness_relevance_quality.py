import pytest
from ai_jobs_v2.models import RawJobListing
from ai_jobs_v2.relevance import classify_relevance
from ai_jobs_v2.connectors.greenhouse import GreenhouseConnector

@pytest.mark.parametrize("title", [
    "Support Engineer", "Senior Applied AI Engineer",
    "Research Engineer - Data Infrastructure",
    "Staff / Principal Research Scientist, Models - USA",
    "Workplace Experience Coordinator, India (Contract)",
    "Adoption Strategist - APAC",
])
def test_corporate_description_does_not_override_title(title):
    raw = RawJobListing("pilot", "Example", "1", title, "https://example.com/jobs/1", "https://example.com/jobs/1", description_text="Our company works on LLM evaluation and data annotation.")
    assert not classify_relevance(raw).is_relevant

@pytest.mark.parametrize("title", ["Software Developer - AI Trainer", "Research Scientist - Freelance AI Trainer Project", "AI Engineer - AI Evaluator", "AI Agentic Workflow Reviewer"])
def test_explicit_evaluation_roles_retained(title):
    raw = RawJobListing("pilot", "Example", "1", title, "https://example.com/jobs/1", "https://example.com/jobs/1", description_text="Perform LLM evaluation and data annotation.")
    assert classify_relevance(raw).is_relevant

@pytest.mark.parametrize("published,expected", [(None,None),("2025-01-01T00:00:00Z","2025-01-01T00:00:00Z")])
def test_greenhouse_publication_not_update(published,expected):
    item = {"id":1,"title":"AI Trainer","first_published":published,"updated_at":"2026-11-26T00:00:00Z","absolute_url":"https://job-boards.greenhouse.io/example/jobs/1"}
    class Response:
        def json(self):
            return {"jobs":[item]}
    class Http:
        def get(self,*args,**kwargs):
            return Response()
    connector = GreenhouseConnector({"id":"greenhouse_example","platform":"Example","official_url":"https://job-boards.greenhouse.io/example","api_endpoint":"https://boards-api.greenhouse.io/v1/boards/example/jobs","method":"greenhouse_api"}, Http())
    assert connector.fetch_listings()[0].official_posted_at == expected
