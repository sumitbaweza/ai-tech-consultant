"""Runs the agent team over one meeting, saving every handoff as a step.

A first meeting:

    1 splitter -> 2 extractor -> 3 merger -> 4 fit checker -> 5 consultant
    -> (waits for a human to answer the questions, if there are any)
    -> 6 estimator <-> 7 reviewer (sent back at most max_review_rounds times)
    -> 8 proposal writer -> draft scope v1 -> (waits for human approval)

A follow-up meeting, once a scope is approved:

    1 splitter -> 2 extractor -> 3 merger -> change analyst -> scope v2
    -> 8 proposal writer -> draft scope v2 -> (waits for human approval)

The run's status says where it is. Each agent's output is saved as a step before the next
agent starts, so the screen shows the work as it happens, and a run that waits for a human
picks up from the saved steps, not from memory. Any error stops the run as FAILED with the
reason; Retry starts its current phase again.
"""

import logging
from collections.abc import Awaitable
from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import store
from app.agents import (
    change_analyst,
    consultant,
    estimator,
    extractor,
    fit_checker,
    merger,
    proposal_writer,
    reviewer,
    splitter,
)
from app.brain_client import Brain
from app.config import Settings
from app.handoffs import (
    ChangeItem,
    Chunk,
    ExtractedItem,
    FitResult,
    Question,
    Requirement,
    Review,
    Scope,
)
from app.llm import Claude
from app.models import Meeting, Project, Run, RunKind, RunStatus, ScopeVersion

logger = logging.getLogger(__name__)


# --- What each step saves (lists get a small wrapper, so every step output is an object) ---


class Split(BaseModel):
    chunks: list[Chunk]


class Requirements(BaseModel):
    requirements: list[Requirement]


class Fits(BaseModel):
    results: list[FitResult]


class Questions(BaseModel):
    questions: list[Question]


class Changes(BaseModel):
    changes: list[ChangeItem]


class Decision(BaseModel):
    """A human's approval or send-back."""

    by: str
    note: str | None
    version: int


@dataclass
class Team:
    """What the agents need: Claude, the Brain, settings, and the database."""

    claude: Claude
    brain: Brain
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]


class Orchestrator:
    def __init__(self, team: Team) -> None:
        self.team = team

    # --- Entry points (each runs as a background task, started by the API) ----------------

    async def start(self, run_id: int) -> None:
        """Run a claimed run from step 1."""
        await self._guard(run_id, self._start(run_id))

    async def resume_with_answers(self, run_id: int) -> None:
        """Continue a first meeting after a human answered (or skipped) the questions."""
        await self._guard(run_id, self._to_draft(run_id))

    async def redo(self, run_id: int, human_note: str | None) -> None:
        """Run the estimate (or the change analysis) again, after a send-back or a failure."""
        await self._guard(run_id, self._redo(run_id, human_note))

    async def _guard(self, run_id: int, work: Awaitable[None]) -> None:
        """Mark the run FAILED, with the reason, if any step raises."""
        try:
            await work
        except Exception as error:
            logger.exception("Run %s failed", run_id)
            async with self.team.sessions() as session:
                await store.set_status(
                    session, run_id, RunStatus.FAILED, f"{type(error).__name__}: {error}"
                )

    # --- Phases ---------------------------------------------------------------------------

    async def _start(self, run_id: int) -> None:
        run, meeting, _ = await self._load(run_id)
        requirements = await self._read_meeting(run, meeting)
        if run.kind == RunKind.FOLLOWUP:
            await self._followup(run, requirements, human_note=None)
            return

        fits = await self._step(
            run_id,
            "fit_checker",
            self._fit(run_id, requirements),
        )
        transcript = _full_text(meeting)
        questions = await self._step(
            run_id,
            "consultant",
            self._clarify(run_id, transcript, requirements, fits.results),
        )
        if questions.questions:
            async with self.team.sessions() as session:
                await store.set_status(session, run_id, RunStatus.AWAITING_ANSWERS)
            return
        await self._to_draft(run_id)

    async def _redo(self, run_id: int, human_note: str | None) -> None:
        run, _, _ = await self._load(run_id)
        if run.kind == RunKind.FOLLOWUP:
            async with self.team.sessions() as session:
                requirements = await store.latest_output(session, run_id, "merger", Requirements)
            if requirements is None:  # it failed before the merger finished: start over
                await self._start(run_id)
                return
            await self._followup(run, requirements.requirements, human_note)
            return
        async with self.team.sessions() as session:
            questions = await store.latest_output(session, run_id, "consultant", Questions)
        if questions is None:  # it failed before the consultant finished: start over
            await self._start(run_id)
            return
        await self._to_draft(run_id, human_note)

    async def _read_meeting(self, run: Run, meeting: Meeting) -> list[Requirement]:
        """Steps 1 to 3, the same for first and follow-up meetings."""
        chunks = await self._step(run.id, "splitter", _split(meeting.transcript))
        extraction = await self._step(
            run.id,
            "extractor",
            extractor.extract(
                self.team.claude,
                chunks.chunks,
                self.team.settings.llm_concurrency,
                store.run_tag(run.id, "extractor"),
            ),
        )
        prefix = "M" if run.kind == RunKind.FOLLOWUP else "R"
        merged = await self._step(run.id, "merger", self._merge(run.id, extraction.items, prefix))
        return merged.requirements

    async def _to_draft(self, run_id: int, human_note: str | None = None) -> None:
        """Steps 6 to 8 of a first meeting: estimate, review, write; then wait for approval."""
        run, meeting, project = await self._load(run_id)
        settings = self.team.settings
        async with self.team.sessions() as session:
            requirements = await store.latest_output(session, run_id, "merger", Requirements)
            fits = await store.latest_output(session, run_id, "fit_checker", Fits)
            answered = await store.latest_output(session, run_id, "human_answers", Questions)
            asked = await store.latest_output(session, run_id, "consultant", Questions)
            draft = await store.run_version(session, run_id)
        if requirements is None or fits is None or asked is None:
            raise RuntimeError("Steps 3 to 5 haven't finished; retry the run")
        questions = (answered or asked).questions
        transcript = _full_text(meeting)
        # A send-back revises the draft as the human left it, edits included
        previous = Scope.model_validate(draft.scope).tasks if draft and human_note else None

        feedback: Review | None = None
        round_number = 0
        while True:
            tag = store.run_tag(run_id, "estimator")
            estimate = await self._step(
                run_id,
                "estimator",
                estimator.estimate(
                    self.team.claude,
                    requirements.requirements,
                    fits.results,
                    questions,
                    settings.scope_rates,
                    feedback=feedback,
                    human_note=human_note,
                    previous=previous,
                    run_tag=tag,
                ),
            )
            review = await self._step(
                run_id,
                "reviewer",
                reviewer.review(
                    self.team.claude,
                    transcript,
                    requirements.requirements,
                    fits.results,
                    questions,
                    estimate.tasks,
                    store.run_tag(run_id, "reviewer"),
                ),
            )
            if review.verdict == "approve" or round_number >= settings.max_review_rounds:
                break
            feedback, previous = review, estimate.tasks
            round_number += 1

        async with self.team.sessions() as session:
            number = await store.next_version_number(session, run.project_id)
        unanswered = [q.default_assumption for q in questions if not q.answer]
        scope = Scope(
            version=draft.version if draft else number,
            requirements=requirements.requirements,
            fit=fits.results,
            questions=questions,
            tasks=estimate.tasks,
            assumptions=estimate.assumptions + unanswered,
            exclusions=estimate.exclusions,
            review=review,
        )
        await self._write_and_save(run, project, scope)

    async def _followup(
        self, run: Run, requirements: list[Requirement], human_note: str | None
    ) -> None:
        """The change analyst compares the meeting with the approved scope; code builds v2."""
        _, _, project = await self._load(run.id)
        async with self.team.sessions() as session:
            if run.base_version_id is None:
                raise RuntimeError("A follow-up run needs an approved version to compare with")
            base = await store.get(session, ScopeVersion, run.base_version_id)
            draft = await store.run_version(session, run.id)
            number = await store.next_version_number(session, run.project_id)
        v1 = Scope.model_validate(base.scope)
        changes = await self._step(
            run.id,
            "change_analyst",
            self._analyze(run.id, v1, requirements, human_note),
        )
        v2 = change_analyst.build_v2(
            v1, requirements, changes.changes, self.team.settings.scope_rates
        )
        v2.version = draft.version if draft else number
        await self._write_and_save(run, project, v2)

    async def _write_and_save(self, run: Run, project: Project, scope: Scope) -> None:
        """Step 8, then save the draft and hand it to a human."""
        scope.proposal = await self._step(
            run.id,
            "proposal_writer",
            proposal_writer.write(
                self.team.claude,
                scope,
                project.name,
                project.client,
                store.run_tag(run.id, "proposal_writer"),
            ),
        )
        async with self.team.sessions() as session:
            await store.save_draft(session, run, scope)
            await store.set_status(session, run.id, RunStatus.AWAITING_APPROVAL)

    # --- Agent calls wrapped so each returns one object to save ---------------------------

    async def _merge(self, run_id: int, items: list[ExtractedItem], prefix: str) -> Requirements:
        tag = store.run_tag(run_id, "merger")
        return Requirements(requirements=await merger.merge(self.team.claude, items, prefix, tag))

    async def _fit(self, run_id: int, requirements: list[Requirement]) -> Fits:
        results = await fit_checker.check_fit(
            self.team.claude,
            self.team.brain,
            requirements,
            self.team.settings.llm_concurrency,
            store.run_tag(run_id, "fit_checker"),
        )
        return Fits(results=results)

    async def _clarify(
        self, run_id: int, transcript: str, requirements: list[Requirement], fits: list[FitResult]
    ) -> Questions:
        tag = store.run_tag(run_id, "consultant")
        questions = await consultant.clarify(self.team.claude, transcript, requirements, fits, tag)
        return Questions(questions=questions)

    async def _analyze(
        self, run_id: int, v1: Scope, requirements: list[Requirement], human_note: str | None
    ) -> Changes:
        changes = await change_analyst.analyze(
            self.team.claude,
            v1,
            requirements,
            human_note=human_note,
            run_tag=store.run_tag(run_id, "change_analyst"),
        )
        return Changes(changes=changes)

    # --- Plumbing -------------------------------------------------------------------------

    async def _step[T: BaseModel](self, run_id: int, agent: str, work: Awaitable[T]) -> T:
        """Run one agent as a saved step: a RUNNING row first, then its output or its error."""
        async with self.team.sessions() as session:
            step_id = await store.start_step(session, run_id, agent)
        try:
            result = await work
        except Exception as error:
            async with self.team.sessions() as session:
                await store.finish_step(session, step_id, error=f"{type(error).__name__}: {error}")
            raise
        async with self.team.sessions() as session:
            await store.finish_step(session, step_id, output=result)
        return result

    async def _load(self, run_id: int) -> tuple[Run, Meeting, Project]:
        async with self.team.sessions() as session:
            run = await store.get(session, Run, run_id)
            meeting = await store.get(session, Meeting, run.meeting_id)
            project = await store.get(session, Project, run.project_id)
        return run, meeting, project


async def _split(transcript: str) -> Split:
    """Step 1 is plain code; as a coroutine it saves (and fails) like the other steps."""
    return Split(chunks=splitter.split(transcript))


def _full_text(meeting: Meeting) -> str:
    """The whole meeting, one turn per line, for the agents that read all of it."""
    return "\n".join(turn.line() for turn in splitter.parse_turns(meeting.transcript))
