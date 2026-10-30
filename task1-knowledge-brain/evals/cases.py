"""The format of evals/cases.json, and the checks a case file must pass before it's run.

The blueprint's 30 cases: 15 how_to, 5 screenshot, 5 unanswerable, 3 follow_up, 2 archived.
"""

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.rag.answer import ChatTurn

CASES_FILE = Path(__file__).parent / "cases.json"


class Kind(StrEnum):
    HOW_TO = "how_to"  # an article's text answers it
    SCREENSHOT = "screenshot"  # only a screenshot answers it
    UNANSWERABLE = "unanswerable"  # the help center doesn't cover it: must say "I don't know"
    FOLLOW_UP = "follow_up"  # makes sense only with the chat before it
    ARCHIVED = "archived"  # its article is archived for the test: must never be cited


class EvalCase(BaseModel):
    # A misspelt field is an error, not silently ignored
    model_config = ConfigDict(extra="forbid")

    # Short and unique, e.g. how-to-text-labels. It becomes part of the run tag in llm_calls.
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=60)
    kind: Kind
    question: str = Field(min_length=1)
    history: list[ChatTurn] = Field(default_factory=list[ChatTurn])
    # The articles that answer it: citing any one of them counts. Empty for unanswerable cases;
    # for an archived case, the article that should be cited instead, if there is one.
    expected_urls: list[str] = Field(default_factory=list[str])
    # The articles to archive while this case runs (archived cases only)
    archive_urls: list[str] = Field(default_factory=list[str])
    # The key facts a correct answer contains, for the answer judge (Day 3)
    expected_answer: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def check_fields_for_kind(self) -> Self:
        if self.kind == Kind.UNANSWERABLE and self.expected_urls:
            raise ValueError("an unanswerable case has no expected_urls")
        if self.kind in (Kind.HOW_TO, Kind.SCREENSHOT, Kind.FOLLOW_UP) and not self.expected_urls:
            raise ValueError(f"a {self.kind} case needs expected_urls")
        if self.kind == Kind.FOLLOW_UP and not self.history:
            raise ValueError("a follow_up case needs the history it follows")
        if (self.kind == Kind.ARCHIVED) != bool(self.archive_urls):
            raise ValueError("archive_urls is for archived cases, and they need it")
        if set(self.archive_urls) & set(self.expected_urls):
            raise ValueError("an article can't be both archived and expected")
        return self


class CaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def check_unique_ids(self) -> Self:
        ids = [case.id for case in self.cases]
        repeated = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
        if repeated:
            raise ValueError(f"ids used more than once: {', '.join(repeated)}")
        return self

    def all_urls(self) -> set[str]:
        return {url for case in self.cases for url in [*case.expected_urls, *case.archive_urls]}


def load_cases(path: Path = CASES_FILE) -> CaseFile:
    return CaseFile.model_validate_json(path.read_text(encoding="utf-8"))
