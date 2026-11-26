"""Regression tests for AI data-work titles that older classifiers missed."""
from ai_jobs_v2.models import RawJobListing
from ai_jobs_v2.relevance import classify_relevance

def raw(title, description=""):
    return RawJobListing(
        source_connector="lever_lionbridge", platform="Lionbridge AI Data Training",
        source_job_id=title.lower(), title=title,
        official_source_url="https://jobs.eu.lever.co/lionbridge",
        raw_apply_url="https://jobs.eu.lever.co/lionbridge",
        location_raw="Remote", description_text=description
    )

def test_language_transcriber_is_relevant():
    result = classify_relevance(raw("Indonesian Transcriber", "Freelance remote data tasks"))
    assert result.is_relevant
    assert result.category == "Audio & Speech Evaluation"

def test_voice_recording_is_relevant():
    assert classify_relevance(raw("Conversational Voice Recording (Arabic ↔ English)")).is_relevant

def test_unrelated_role_with_transcription_in_description_is_not_automatically_relevant():
    assert not classify_relevance(raw("Corporate Accountant", "A service that includes transcription of reports.")).is_relevant
