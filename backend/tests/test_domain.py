"""Domain foundation tests (ARCHITECTURE.md §4.3, §21, §36; PRD §15.2)."""

from app.domain.models import (
    JOB_PIPELINE_ORDER,
    CorpusType,
    FeedbackVote,
    JobFailure,
    JobStatus,
    MessageRole,
)


def test_job_pipeline_order_matches_ingestion_lifecycle() -> None:
    assert JOB_PIPELINE_ORDER == (
        JobStatus.QUEUED,
        JobStatus.PARSING,
        JobStatus.CHUNKING,
        JobStatus.EMBEDDING,
        JobStatus.INDEXING,
        JobStatus.READY,
    )


def test_job_status_values_are_stable_strings() -> None:
    assert JobStatus.QUEUED.value == "queued"
    assert JobStatus.FAILED.value == "failed"


def test_message_roles() -> None:
    assert {role.value for role in MessageRole} == {"user", "assistant", "system"}


def test_feedback_votes() -> None:
    assert {vote.value for vote in FeedbackVote} == {"up", "down"}


def test_corpus_types_are_distinct() -> None:
    assert CorpusType.BNS.value == "bns"
    assert CorpusType.USER_DOCUMENT.value == "user_document"


def test_job_failure_round_trip() -> None:
    failure = JobFailure(error_code="PARSE_FAILED", error_message="could not parse")
    assert failure.model_dump() == {
        "error_code": "PARSE_FAILED",
        "error_message": "could not parse",
    }
