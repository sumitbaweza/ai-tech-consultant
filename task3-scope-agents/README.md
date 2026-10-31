# Task 3: Meeting to Scope (Python)

A team of agents turns client meeting transcripts into a scope of work, a task list with hour ranges, and a client-ready proposal. It checks the Knowledge Brain (Task 1) for settings the product already has, asks the questions a senior consultant would ask, and turns a follow-up meeting into scope v2 with a priced change order. Nothing reaches the client until a human approves it.

- Screens: http://localhost:8502 (`scope-ui`, Streamlit)
- API and its docs: http://localhost:8002/docs (`scope-api`, FastAPI; key: `SCOPE_API_KEYS` in `.env`)
- Both start with everything else: `make up`, or `docker compose up -d --build`

## The team

One file per agent in `app/agents/`; every handoff is a Pydantic object from `app/handoffs.py`, saved as a step so the screen shows it.

| Step | File | Model | Job | Hands on |
| --- | --- | --- | --- | --- |
| 1 Splitter | `splitter.py` | none | Reads the transcript format; cuts it into ~3,000-token chunks between speaker turns, with one turn of overlap | `Chunk` |
| 2 Extractor | `extractor.py` | Haiku 5.5, one call per chunk, in parallel | Lists requirements, each with the client's exact quote; **code drops any quote that isn't in the transcript** | `ExtractedItem` |
| 3 Merger | `merger.py` | Sonnet 5.5 | Merges duplicates, flags conflicts; code ignores invented ids and keeps any item the model left out | `Requirement` |
| 4 Fit checker | `fit_checker.py` | Sonnet 5.5 + the Brain | Brain `/v1/search` for each requirement; Sonnet says setting, small change or custom, citing only real hits; for settings, Brain `/v1/ask` gives the cited steps | `FitResult` |
| 5 Consultant | `consultant.py` | Sonnet 5.5 | Clarifying questions, each with a default assumption. **The run waits for a human**; blank answers become assumptions | `Question` |
| 6 Estimator | `estimator.py` | Sonnet 5.5 | Tasks with low, likely and high hours; code prices them (PERT × rate card) | `Task` |
| 7 Reviewer | `reviewer.py` | Opus 5.5 | Code checks the joins (every requirement has a task, every task a requirement, settings priced as settings); Opus reads the meeting and the estimate. Sends it back to step 6 at most `MAX_REVIEW_ROUNDS` times (default 1) | `Review` |
| 8 Proposal writer | `proposal_writer.py` | Sonnet 5.5 | Writes the words only; code renders every number from the scope | `ProposalDraft` |
| Human approval | `human_approval.py` | none | Edit hours and tasks, approve, or send back with a note. Edits are recorded against the estimator's own hours | `EditRecord` |
| Change analyst | `change_analyst.py` | Sonnet 5.5 | Follow-up meetings: steps 1–3 again, then reads the new requirements **and the transcript** and labels each item unchanged, changed, new or removed against the approved scope (a removal must quote the client); code builds v2 and the change order | `ChangeItem` |
| Learner | `learner.py` | Sonnet 5.5, only when a human edited hours | After an approval, turns the hour edits into estimation rules: a new playbook version that the estimator follows from then on | `EstimationRule` |

`app/orchestrator.py` runs them in order and saves each output before the next agent starts. A run's status says where it is: `running`, `awaiting_answers`, `awaiting_approval`, `approved` or `failed` (with the reason; **Retry** resumes from the saved steps).

## How it learns

Every task keeps the estimator's own hours (`ai_hours`) next to the hours a human approved. After an approval with hour edits, the learner updates the **estimation playbook** (`scope.playbook_versions`): existing rules kept or revised, at most three new ones, saved as a new version. The estimator (prompt `estimate_tasks` v2) follows the active version, and each scope records which version it used. The **Learning** screen (and `GET /v1/playbook`) shows the gap between AI and approved hours per playbook version, lower is better, and makes an older version active again if a lesson made things worse (`POST /v1/playbook/{version}/activate`).

## Rules it keeps

- **Task 1 only through its API.** `app/brain_client.py` calls `POST /v1/search` and `POST /v1/ask` with `BRAIN_API_KEY`. If the Brain is down, refuses the key, or has nothing close, the requirement is `unverified`, priced as custom work, and the run goes on.
- **Transcripts are data.** They go inside `<transcript>` tags that a speaker can't close (`app/text.py` escapes `<` and `>`), every prompt says so, and every requirement keeps its quote, speaker and time. `tests/test_agents.py` runs a transcript with an injection attempt.
- **One Claude wrapper.** `app/llm.py`: versioned prompts (`prompts/<name>/vN.md`), structured outputs, `stop_reason` checks, retries, a fallback model, and a row in `scope.llm_calls` per call, tagged `run-<id>/<agent>`. The screen shows each run's cost by agent.
- **Human gate.** A draft can't be exported: `GET /v1/versions/{id}/proposal.md` answers 409 until a human approves, and the preview says DRAFT. An approved version is frozen; a follow-up makes the next version, and the old one is kept as `superseded`.

## Try it

1. Open http://localhost:8502 and enter your name in the sidebar.
2. **New meeting**: create a project, pick `01-first-meeting-bright-smile.txt`, start the agents.
3. **Agent run**: watch each step; answer the consultant's questions (or leave some blank).
4. **Scope and approval**: edit hours, then approve (or send back with a note); download the proposal.
5. **New meeting** again, same project, `03-followup-bright-smile.txt`: the follow-up becomes v2 with changed, new and removed items and a change order. Compare it with v1 on the **Changes vs previous** tab.

The transcripts in `transcripts/` are hand-written stand-ins until the recorded role-play meetings are in (see `transcripts/README.md`).

## Develop

On Windows uv's managed Python link can break; if `uv run` says "Missing expected target directory", point it at the venv: `$env:UV_PYTHON = ".venv\Scripts\python.exe"` (PowerShell).

| Make target | Without make |
| --- | --- |
| `make scope-test` | `uv run --directory task3-scope-agents pytest` |
| `make scope-lint` | `uv run --directory task3-scope-agents ruff check .`, `ruff format --check .`, `pyright` |
| `make scope-format` | `uv run --directory task3-scope-agents ruff check --fix .`, `ruff format .` |

Tests use a fake Claude (an httpx2 mock transport answering each prompt with a scripted reply) and a fake Brain (respx). Database tests run against `consultant_test` and are skipped when Postgres isn't up. Eval sets are not part of this folder yet.
