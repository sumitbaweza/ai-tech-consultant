"""The typed objects the agents hand to each other.

Each agent takes some of these and returns others, so every handoff can be shown on screen,
saved as JSON, and checked in a test. What a model replies with is a separate, smaller schema
inside each agent's file; the agent's code checks that reply and turns it into these objects.

    Splitter -> Chunk -> Extractor -> ExtractedItem -> Merger -> Requirement
    -> Fit checker -> FitResult -> Consultant -> Question -> (human answers)
    -> Estimator -> Task -> Reviewer -> Review -> Proposal writer -> ProposalDraft
    -> Scope (version 1) -> human approval
    Follow-up: Splitter -> Extractor -> Merger -> Change analyst -> ChangeItem -> Scope (v2)
"""

from typing import Literal

from pydantic import BaseModel, Field, computed_field

Priority = Literal["must", "should", "could"]
# setting: the product already does it, only configuration; small_change: a setting plus a
# little work; custom: new code; unverified: the Brain couldn't say (down, or found nothing)
FitKind = Literal["setting", "small_change", "custom", "unverified"]
Role = Literal["developer", "designer", "consultant", "qa"]
ChangeLabel = Literal["unchanged", "changed", "new", "removed"]


# --- Splitter -----------------------------------------------------------------------------


class Turn(BaseModel):
    """One speaker turn of a transcript."""

    index: int
    speaker: str
    time: str | None  # "hh:mm:ss" when the transcript has times
    text: str

    def line(self) -> str:
        stamp = f"[{self.time}] " if self.time else ""
        return f"{stamp}{self.speaker}: {self.text}"


class Chunk(BaseModel):
    """A run of whole turns, small enough for one extractor call."""

    index: int
    turns: list[Turn]

    @property
    def start(self) -> str | None:
        return self.turns[0].time

    @property
    def end(self) -> str | None:
        return self.turns[-1].time

    @property
    def text(self) -> str:
        return "\n".join(turn.line() for turn in self.turns)


# --- Extractor and merger -----------------------------------------------------------------


class Quote(BaseModel):
    """The client's own words, found in the transcript by code, with who said them and when."""

    text: str
    speaker: str
    time: str | None


class ExtractedItem(BaseModel):
    """One requirement found in one chunk. Its quote was checked against the chunk's text."""

    id: str  # "C2-3": chunk 2, item 3
    chunk_index: int
    title: str
    detail: str
    quote: Quote
    priority: Priority
    is_vague: bool


class Requirement(BaseModel):
    """One thing the client wants, after duplicates across chunks were merged."""

    id: str  # "R1", "R2", ...
    title: str
    detail: str
    priority: Priority
    is_vague: bool
    quotes: list[Quote] = Field(min_length=1)
    source_ids: list[str]  # the ExtractedItem ids merged into this one
    conflict: str | None = None  # set when the client said different things at different times
    # Follow-up meetings only: what happened to this requirement in scope v2
    change: ChangeLabel | None = None
    removed: bool = False


# --- Fit checker --------------------------------------------------------------------------


class ArticleLink(BaseModel):
    title: str | None
    url: str


class FitResult(BaseModel):
    """Whether the product can already do a requirement, according to the Knowledge Brain."""

    requirement_id: str
    kind: FitKind
    reason: str
    setting_path: str | None = None  # where the setting lives, e.g. "Settings > Search Filters"
    how_to: str | None = None  # the Brain's cited steps, for settings
    articles: list[ArticleLink] = []
    # ok: the Brain answered; no_match: it searched and found nothing close;
    # unavailable: it couldn't be reached; error: the fit check itself failed
    brain: Literal["ok", "no_match", "unavailable", "error"]


# --- Consultant ---------------------------------------------------------------------------


class Question(BaseModel):
    """A question a senior consultant would ask the client before quoting."""

    id: str  # "Q1", ...
    requirement_id: str | None  # None for a question about the whole project
    question: str
    why_it_matters: str
    # Used, and written into the proposal as an assumption, if nobody answers
    default_assumption: str
    answer: str | None = None

    @property
    def resolution(self) -> str:
        if self.answer:
            return f"Answered: {self.answer}"
        return f"Assumed: {self.default_assumption}"


# --- Estimator and reviewer ---------------------------------------------------------------


class Hours(BaseModel):
    low: float = Field(ge=0)
    likely: float = Field(ge=0)
    high: float = Field(ge=0)

    @property
    def expected(self) -> float:
        """The PERT estimate: a range's likely value counts four times its ends."""
        return round((self.low + 4 * self.likely + self.high) / 6, 1)


class Task(BaseModel):
    """One piece of work with an hours range and a price."""

    id: str  # "T1", ...
    requirement_ids: list[str]
    title: str
    description: str
    role: Role
    low: float = Field(ge=0)
    likely: float = Field(ge=0)
    high: float = Field(ge=0)
    rate: float = Field(ge=0)  # dollars per hour, from the rate card when the task was priced
    basis: str  # why these hours
    # estimate: from the estimator; change: from a follow-up meeting; human: added by hand
    origin: Literal["estimate", "change", "human"] = "estimate"
    # True for extra work outside the approved scope, quoted in the change order
    change_order: bool = False
    # The estimator's hours before anyone edited them: the gap is how estimates get measured
    ai_hours: Hours | None = None

    @computed_field
    @property
    def expected(self) -> float:
        return Hours(low=self.low, likely=self.likely, high=self.high).expected

    @computed_field
    @property
    def price(self) -> float:
        return round(self.expected * self.rate, 2)


class Coverage(BaseModel):
    """The reviewer's checks that need no model: do requirements and tasks join up?"""

    uncovered_requirements: list[str]  # no task does them
    orphan_tasks: list[str]  # do no requirement
    settings_priced_as_custom: list[str]  # a "setting" requirement with over 2 likely hours

    @property
    def has_gaps(self) -> bool:
        return bool(self.uncovered_requirements or self.orphan_tasks)


class ReviewIssue(BaseModel):
    target: str  # a task or requirement id, or "scope"
    problem: str
    fix: str


class Review(BaseModel):
    """The senior reviewer's verdict on an estimate."""

    verdict: Literal["approve", "send_back"]
    summary: str
    issues: list[ReviewIssue]


# --- Change analyst -----------------------------------------------------------------------


class ChangeItem(BaseModel):
    """One line of the diff between the approved scope and a follow-up meeting."""

    label: ChangeLabel
    title: str
    v1_requirement_id: str | None  # the approved requirement it repeats, changes or removes
    followup_id: str | None  # the follow-up meeting's requirement (M1, ...)
    v2_requirement_id: str | None = None  # where it lands in the new scope, set by build_v2
    before: str | None  # what was agreed
    after: str | None  # what the client asks for now
    quote: Quote | None
    # Extra work counts as in scope when it only clarifies what was already agreed
    in_signed_scope: bool
    extra_hours: Hours | None
    role: Role = "developer"
    reason: str


# --- Proposal writer ----------------------------------------------------------------------


class ProposalDraft(BaseModel):
    """The proposal's words. Every number in the proposal comes from the Scope, not from here."""

    title: str
    summary: str
    understanding: str  # the client's goals, in their terms
    approach: str
    settings_note: str  # what existing settings solve, and what that saves the client
    timeline: str
    next_steps: str


# --- The scope ----------------------------------------------------------------------------


class Totals(BaseModel):
    low_hours: float
    expected_hours: float
    high_hours: float
    price: float
    price_low: float
    price_high: float
    change_order_hours: float
    change_order_price: float


class Scope(BaseModel):
    """A scope version: what the client gets, what it costs, and what it rests on."""

    version: int
    based_on_version: int | None = None
    requirements: list[Requirement]
    fit: list[FitResult]
    questions: list[Question]
    tasks: list[Task]
    assumptions: list[str]
    exclusions: list[str]
    changes: list[ChangeItem] = []  # follow-up versions only
    review: Review | None = None
    proposal: ProposalDraft | None = None

    @computed_field
    @property
    def totals(self) -> Totals:
        low = sum(t.low * t.rate for t in self.tasks)
        high = sum(t.high * t.rate for t in self.tasks)
        extras = [t for t in self.tasks if t.change_order]
        return Totals(
            low_hours=round(sum(t.low for t in self.tasks), 1),
            expected_hours=round(sum(t.expected for t in self.tasks), 1),
            high_hours=round(sum(t.high for t in self.tasks), 1),
            price=round(sum(t.price for t in self.tasks), 2),
            price_low=round(low, 2),
            price_high=round(high, 2),
            change_order_hours=round(sum(t.expected for t in extras), 1),
            change_order_price=round(sum(t.price for t in extras), 2),
        )

    def active_requirements(self) -> list[Requirement]:
        return [r for r in self.requirements if not r.removed]
