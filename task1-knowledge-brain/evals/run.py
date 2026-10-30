"""Run the Task 1 eval: each case in evals/cases.json goes through the real answer path, ask().

    uv run python -m evals.run            # every case; needs both API keys
    uv run python -m evals.run --check    # check the case file against the crawl: no keys, no cost
    uv run python -m evals.run --only how-to-rename-a-word-everywhere
    uv run python -m evals.run --expert-replay   # ask each expert answer's question again

A case passes when the answer does what its kind requires:
- how_to and follow_up: answered, citing an expected article
- screenshot: answered, citing a screenshot from an expected article
- unanswerable: an honest "I don't know"; a model failure doesn't count as one
- archived: never cites the archived article, and cites the expected one if the case names one
Whether the answer's facts are right is for the answer judge (Day 3), which compares the answer
with the case's expected_answer.

Also measured for each case: whether an expected article is among the first TOP_K articles
search returns, the latency of ask(), and its cost, added up from the llm_calls rows tagged
with the case's run tag. A table is printed, and the results are saved to
evals/results/<run id>.json.

The expert replay measures what the Brain learned from its experts. Each expert answer is a
question the Brain once failed, so the replay asks each one again and counts how many are
answered now, and how many from the expert's answer. Its results go to
evals/results/expert-replay-<run id>.json.
"""

import argparse
import asyncio
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db import SessionFactory, engine
from app.llm import Claude, get_claude
from app.models import Chunk, Image, LLMCall, Page, PageKind, PageStatus
from app.rag.answer import AskResult, Outcome, ask
from app.rag.embed import Embedder, get_voyage
from app.rag.search import search
from evals.cases import CASES_FILE, CaseFile, EvalCase, Kind, load_cases

RESULTS_DIR = Path(__file__).parent / "results"
TOP_K = 5  # the blueprint's measure: the right article in the top 5
SEARCH_DEPTH = 20  # chunks searched to find those 5 articles, as one article can fill several


class CheckReport(BaseModel):
    problems: list[str]  # these make a run meaningless: fix the case file
    not_indexed: list[str]  # crawled articles with no chunks yet: wait for the worker


class CaseResult(BaseModel):
    id: str
    kind: Kind
    question: str
    standalone_question: str  # what was searched: a follow-up, rewritten
    passed: bool
    failures: list[str]  # why it didn't pass, in words
    outcome: Outcome
    answer: str
    cited_urls: list[str]
    top_articles: list[str]  # the first TOP_K distinct articles search returned
    in_top_k: bool | None  # None when the case has no expected article
    latency_ms: int  # of ask(), the whole answer path
    # Claude only: the question's embedding costs about $0.0000004 and isn't tagged
    cost_usd: float
    claude_calls: int  # our wrapper's attempts, fallbacks included


class Tally(BaseModel):
    hits: int
    total: int

    def __str__(self) -> str:
        return f"{self.hits}/{self.total}"


class Summary(BaseModel):
    passed: Tally
    passed_by_kind: dict[Kind, Tally]
    in_top_k: Tally  # over the cases with an expected article
    latency_p50_ms: int
    latency_p95_ms: int
    cost_usd: float
    cost_per_case_usd: float


class Report(BaseModel):
    run_id: str
    started_at: datetime
    cases_file: str
    models: dict[str, int]  # e.g. "answer v1 on claude-sonnet-5-5": number of calls
    summary: Summary
    results: list[CaseResult]


class ReplayResult(BaseModel):
    page_id: int  # the expert answer's page
    question: str
    outcome: Outcome
    cites_expert: bool  # answered from the expert's answer
    latency_ms: int
    cost_usd: float
    claude_calls: int


class ReplayReport(BaseModel):
    run_id: str
    started_at: datetime
    answered: Tally  # once-failed questions that are answered now
    cites_expert: Tally  # the ones answered from the expert's answer
    latency_p50_ms: int
    latency_p95_ms: int
    cost_usd: float
    results: list[ReplayResult]


# --- Checking the case file against the crawl -----------------------------------------------


async def check_cases(session: AsyncSession, case_file: CaseFile) -> CheckReport:
    """Every URL must be a crawled, active article, and a screenshot case's article must have
    screenshots. Articles without chunks are listed apart: the case is fine, the index isn't
    ready yet."""
    images = select(func.count()).where(Image.page_id == Page.id).scalar_subquery()
    chunks = select(func.count()).where(Chunk.page_id == Page.id).scalar_subquery()
    rows = await session.execute(
        select(
            Page.url, Page.kind, Page.status, images.label("images"), chunks.label("chunks")
        ).where(Page.url.in_(case_file.all_urls()))
    )
    pages = {row.url: row for row in rows}

    problems: list[str] = []
    not_indexed: set[str] = set()
    for case in case_file.cases:
        for url in [*case.expected_urls, *case.archive_urls]:
            page = pages.get(url)
            if page is None:
                problems.append(f"{case.id}: never crawled: {url}")
            elif page.kind != PageKind.ARTICLE:
                problems.append(f"{case.id}: a {page.kind} page, not an article: {url}")
            elif page.status != PageStatus.ACTIVE:
                problems.append(f"{case.id}: {page.status}, not active: {url}")
            elif case.kind == Kind.SCREENSHOT and page.images == 0:
                problems.append(f"{case.id}: has no screenshots: {url}")
            elif page.chunks == 0:
                not_indexed.add(url)
    return CheckReport(problems=problems, not_indexed=sorted(not_indexed))


# --- Running and scoring one case -----------------------------------------------------------


async def run_case(
    session_factory: async_sessionmaker[AsyncSession],
    claude: Claude,
    embedder: Embedder,
    case: EvalCase,
    run_id: str,
) -> CaseResult:
    run_tag = f"eval:{run_id}:{case.id}"
    async with session_factory() as session:
        # Archiving is never committed: this transaction is rolled back below, so no other
        # connection ever sees it, and a crash can't leave the article archived
        if case.archive_urls:
            await session.execute(
                update(Page)
                .where(Page.url.in_(case.archive_urls))
                .values(status=PageStatus.ARCHIVED)
            )
        started = time.perf_counter()
        result = await ask(session, claude, embedder, case.question, case.history, run_tag=run_tag)
        latency_ms = round((time.perf_counter() - started) * 1000)
        top = await top_articles(session, embedder, result.standalone_question)
        await session.rollback()
        cost, calls = await tagged_cost(session, run_tag)

    failures = score(case, result)
    expected = set(case.expected_urls)
    return CaseResult(
        id=case.id,
        kind=case.kind,
        question=case.question,
        standalone_question=result.standalone_question,
        passed=not failures,
        failures=failures,
        outcome=result.outcome,
        answer=result.answer,
        cited_urls=list(dict.fromkeys(citation.url for citation in result.citations)),
        top_articles=top,
        in_top_k=bool(expected & set(top)) if expected else None,
        latency_ms=latency_ms,
        cost_usd=cost,
        claude_calls=calls,
    )


async def tagged_cost(session: AsyncSession, run_tag: str) -> tuple[float, int]:
    """What the Claude calls tagged with run_tag cost, and how many there were. The ledger saves
    each call in its own transaction, so a rolled-back case keeps them."""
    cost, calls = (
        await session.execute(
            select(func.coalesce(func.sum(LLMCall.cost_usd), 0), func.count()).where(
                LLMCall.run_tag == run_tag
            )
        )
    ).one()
    return float(cost), calls


async def top_articles(session: AsyncSession, embedder: Embedder, question: str) -> list[str]:
    """The first TOP_K distinct articles that search returns for the question ask() searched.
    It runs in the case's session, so an article archived for the case is left out here too."""
    [vector] = await embedder.embed([question], "query")
    hits = await search(session, question, vector, limit=SEARCH_DEPTH)
    return list(dict.fromkeys(hit.url for hit in hits))[:TOP_K]


def score(case: EvalCase, result: AskResult) -> list[str]:
    """Why the case failed, one reason each. No reasons means it passed."""
    if result.outcome == Outcome.MODEL_FAILED:
        return ["Claude refused, broke the format or wasn't reachable"]
    if case.kind == Kind.UNANSWERABLE:
        return ["answered a question the help center doesn't cover"] if result.answered else []

    failures: list[str] = []
    cited = {citation.url for citation in result.citations}
    if cited & set(case.archive_urls):
        failures.append("cited the archived article")
    if case.expected_urls:
        expected = set(case.expected_urls)
        if not result.answered:
            failures.append(f"didn't answer ({result.outcome})")
        elif not cited & expected:
            failures.append("cited no expected article")
        elif case.kind == Kind.SCREENSHOT and not any(
            citation.image_url is not None and citation.url in expected
            for citation in result.citations
        ):
            failures.append("cited no screenshot from an expected article")
    return failures


# --- The whole run --------------------------------------------------------------------------


def summarize(results: list[CaseResult]) -> Summary:
    by_kind: dict[Kind, list[bool]] = {}
    for result in results:
        by_kind.setdefault(result.kind, []).append(result.passed)
    latencies = [result.latency_ms for result in results]
    cost = sum(result.cost_usd for result in results)
    return Summary(
        passed=tally([result.passed for result in results]),
        passed_by_kind={kind: tally(passed) for kind, passed in by_kind.items()},
        in_top_k=tally([result.in_top_k for result in results if result.in_top_k is not None]),
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
        cost_usd=round(cost, 6),
        cost_per_case_usd=round(cost / len(results), 6),
    )


def tally(checks: list[bool]) -> Tally:
    return Tally(hits=sum(checks), total=len(checks))


def percentile(values: list[int], fraction: float) -> int:
    """Nearest rank: the smallest value with at least `fraction` of all the values at or
    below it. With 30 cases, p95 is the second slowest."""
    ordered = sorted(values)
    return ordered[max(math.ceil(fraction * len(ordered)), 1) - 1]


async def models_used(session: AsyncSession, run_id: str) -> dict[str, int]:
    """Which prompt ran on which model, and how often. A fallback model shows up here."""
    rows = await session.execute(
        select(LLMCall.prompt, LLMCall.prompt_version, LLMCall.model, func.count())
        .where(LLMCall.run_tag.startswith(f"eval:{run_id}:"))
        .group_by(LLMCall.prompt, LLMCall.prompt_version, LLMCall.model)
        .order_by(LLMCall.prompt, LLMCall.model)
    )
    return {f"{prompt} v{version} on {model}": count for prompt, version, model, count in rows}


def case_line(result: CaseResult) -> str:
    top = "-" if result.in_top_k is None else "yes" if result.in_top_k else "no"
    line = (
        f"{'PASS' if result.passed else 'FAIL'}  {result.outcome:<17}  top {TOP_K}: {top:<3}  "
        f"{result.latency_ms / 1000:5.1f} s  ${result.cost_usd:.4f}  {result.id}"
    )
    return "\n".join([line, *(f"      - {failure}" for failure in result.failures)])


def print_summary(report: Report) -> None:
    summary = report.summary
    kinds = ", ".join(f"{kind} {passed}" for kind, passed in summary.passed_by_kind.items())
    print(f"\nPassed {summary.passed} ({kinds})")
    print(f"An expected article in the top {TOP_K}: {summary.in_top_k}")
    p50, p95 = summary.latency_p50_ms / 1000, summary.latency_p95_ms / 1000
    print(f"Latency of ask(): p50 {p50:.1f} s, p95 {p95:.1f} s")
    print(f"Claude cost: ${summary.cost_usd:.4f} in all, ${summary.cost_per_case_usd:.4f} a case")
    models = ", ".join(f"{name} x{count}" for name, count in report.models.items())
    print(f"Models: {models or 'none'}")


# --- The expert replay ----------------------------------------------------------------------


async def replay_experts(
    session_factory: async_sessionmaker[AsyncSession],
    claude: Claude,
    embedder: Embedder,
    run_id: str,
) -> list[ReplayResult]:
    """Ask the question of each expert answer in the knowledge base again, one at a time."""
    async with session_factory() as session:
        pages = (
            await session.scalars(
                select(Page)
                .where(Page.kind == PageKind.EXPERT, Page.status == PageStatus.ACTIVE)
                .order_by(Page.id)
            )
        ).all()

    results: list[ReplayResult] = []
    for page in pages:
        run_tag = f"replay:{run_id}:{page.id}"
        question = page.title or ""  # an expert page's title is its question
        async with session_factory() as session:
            started = time.perf_counter()
            result = await ask(session, claude, embedder, question, [], run_tag=run_tag)
            latency_ms = round((time.perf_counter() - started) * 1000)
            cost, calls = await tagged_cost(session, run_tag)
        replayed = ReplayResult(
            page_id=page.id,
            question=question,
            outcome=result.outcome,
            cites_expert=any(citation.page_id == page.id for citation in result.citations),
            latency_ms=latency_ms,
            cost_usd=cost,
            claude_calls=calls,
        )
        print(replay_line(replayed))
        results.append(replayed)
    return results


def replay_line(result: ReplayResult) -> str:
    verdict = (
        "EXPERT"
        if result.cites_expert
        else "ANSWERED"
        if result.outcome == Outcome.ANSWERED
        else "MISSED"
    )
    return (
        f"{verdict:<8}  {result.outcome:<17}  {result.latency_ms / 1000:5.1f} s  "
        f"${result.cost_usd:.4f}  {result.question}"
    )


async def expert_replay(claude: Claude, embedder: Embedder) -> int:
    async with SessionFactory() as session:
        waiting = await session.scalar(
            select(func.count())
            .select_from(Page)
            .where(
                Page.kind == PageKind.EXPERT,
                Page.status == PageStatus.ACTIVE,
                Page.embedded_hash.is_distinct_from(Page.content_hash),
            )
        )
    if waiting:
        print(f"Expert answers not embedded yet: {waiting}. The worker embeds them in a minute.")
        return 1

    started_at = datetime.now(UTC)
    run_id = started_at.strftime("%Y%m%d-%H%M%S")
    print(f"\nExpert replay {run_id}\n")
    results = await replay_experts(SessionFactory, claude, embedder, run_id)
    if not results:
        print("There are no expert answers in the knowledge base yet")
        return 0

    latencies = [result.latency_ms for result in results]
    report = ReplayReport(
        run_id=run_id,
        started_at=started_at,
        answered=tally([result.outcome == Outcome.ANSWERED for result in results]),
        cites_expert=tally([result.cites_expert for result in results]),
        latency_p50_ms=percentile(latencies, 0.50),
        latency_p95_ms=percentile(latencies, 0.95),
        cost_usd=round(sum(result.cost_usd for result in results), 6),
        results=results,
    )
    RESULTS_DIR.mkdir(exist_ok=True)
    saved = RESULTS_DIR / f"expert-replay-{run_id}.json"
    saved.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print(
        f"\nOnce-failed questions answered now: {report.answered} "
        f"({report.cites_expert} from the expert's answer)"
    )
    p50, p95 = report.latency_p50_ms / 1000, report.latency_p95_ms / 1000
    print(
        f"Latency of ask(): p50 {p50:.1f} s, p95 {p95:.1f} s. Claude cost: ${report.cost_usd:.4f}"
    )
    print(f"\nSaved evals/results/{saved.name}")
    return 0


# --- The command line -----------------------------------------------------------------------


def real_clients() -> tuple[Claude, Embedder] | None:
    """Claude and Voyage, or None after saying that a key is missing."""
    embedder = get_voyage()
    if embedder is None or not get_settings().anthropic_api_key.get_secret_value():
        print("This needs ANTHROPIC_API_KEY and VOYAGE_API_KEY in .env")
        return None
    return get_claude(), embedder


async def check(case_file: CaseFile, cases_path: Path) -> CheckReport:
    async with SessionFactory() as session:
        report = await check_cases(session, case_file)
    if report.problems:
        print(f"{len(report.problems)} problems in {cases_path.name}:")
        print("\n".join(f"  {problem}" for problem in report.problems))
    else:
        print(f"{cases_path.name}: {len(case_file.cases)} cases, every article crawled and active")
    if report.not_indexed:
        print(f"Not indexed yet, so search can't find them ({len(report.not_indexed)}):")
        print("\n".join(f"  {url}" for url in report.not_indexed))
        print("The worker indexes them once VOYAGE_API_KEY is set; GET /v1/stats shows progress.")
    return report


async def run(cases: list[EvalCase], cases_path: Path, claude: Claude, embedder: Embedder) -> None:
    started_at = datetime.now(UTC)
    run_id = started_at.strftime("%Y%m%d-%H%M%S")
    print(f"\nEval run {run_id}: {len(cases)} cases\n")
    results: list[CaseResult] = []
    for case in cases:  # one at a time, so the latencies aren't skewed by each other
        result = await run_case(SessionFactory, claude, embedder, case, run_id)
        print(case_line(result))
        results.append(result)

    async with SessionFactory() as session:
        models = await models_used(session, run_id)
    report = Report(
        run_id=run_id,
        started_at=started_at,
        cases_file=cases_path.name,
        models=models,
        summary=summarize(results),
        results=results,
    )
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / f"{run_id}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    print_summary(report)
    print(f"\nSaved evals/results/{run_id}.json")


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run", description="The Task 1 eval")
    parser.add_argument(
        "--check", action="store_true", help="only check the cases against the crawl: no cost"
    )
    parser.add_argument("--only", nargs="+", default=[], metavar="ID", help="run only these cases")
    parser.add_argument("--cases", type=Path, default=CASES_FILE, help="another case file")
    parser.add_argument(
        "--expert-replay", action="store_true", help="ask each expert answer's question again"
    )
    args = parser.parse_args(argv)
    cases_path: Path = args.cases
    only: list[str] = args.only

    if args.expert_replay:
        try:
            clients = real_clients()
            return 1 if clients is None else await expert_replay(*clients)
        finally:
            await engine.dispose()

    try:
        case_file = load_cases(cases_path)
    except ValidationError as error:
        print(f"{cases_path.name} has mistakes:\n{error}")
        return 1
    unknown = set(only) - {case.id for case in case_file.cases}
    if unknown:
        print(f"No case with the id {', '.join(sorted(unknown))}")
        return 1

    try:
        report = await check(case_file, cases_path)
        if report.problems:
            return 1
        if args.check:
            return 0
        clients = real_clients()
        if clients is None or report.not_indexed:
            return 1
        cases = [case for case in case_file.cases if not only or case.id in only]
        await run(cases, cases_path, *clients)
        return 0
    finally:
        await engine.dispose()  # close the pooled connections before the event loop closes


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
