from datetime import timedelta

from app.models import Base, Retention, Schedule


def test_every_table_is_in_the_brain_schema() -> None:
    assert set(Base.metadata.tables) == {
        "brain.sitemaps",
        "brain.ingestion_runs",
        "brain.pages",
        "brain.page_sitemaps",
        "brain.page_versions",
        "brain.images",
        "brain.chunks",
        "brain.expert_tasks",
        "brain.llm_calls",
    }


def test_schedules_match_test_2() -> None:
    assert [schedule.interval for schedule in Schedule] == [
        timedelta(days=1),
        timedelta(days=7),
        timedelta(days=15),
        timedelta(days=30),
    ]


def test_retention_rules_match_test_2() -> None:
    # "Archive immediately" means the first check that misses the URL archives it
    assert [rule.missing_checks for rule in Retention] == [1, 2, 15, 30]
