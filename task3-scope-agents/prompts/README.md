# Prompts

Each prompt is a folder with one file per version: `estimate_tasks/v1.md`, `estimate_tasks/v2.md`, and so on. `app/llm.py` loads a prompt by name and version, and every row in `scope.llm_calls` records which version ran, tagged with the run and agent (`run-12/estimator`). That's how an eval result or a cost ties back to the exact prompt text.

| Prompt | Agent | Model | Why this model |
| --- | --- | --- | --- |
| `extract_requirements` | 2 · Extractor | Haiku 5.5 | One call per transcript chunk; simple extraction at a twentieth of Sonnet's price |
| `merge_requirements` | 3 · Merger | Sonnet 5.5 | Deciding that two phrasings mean the same thing needs judgment |
| `check_fit` | 4 · Fit checker | Sonnet 5.5 | Setting vs custom code is the decision that saves the client money |
| `clarify_requirements` | 5 · Consultant | Sonnet 5.5 | Good questions need the whole meeting |
| `estimate_tasks` | 6 · Estimator | Sonnet 5.5 | Hours need judgment; runs once or twice per meeting |
| `review_scope` | 7 · Reviewer | Opus 5.5 | One call per scope; a stronger model than the one it checks |
| `write_proposal` | 8 · Proposal writer | Sonnet 5.5 | Client-facing words; no numbers (code renders those) |
| `analyze_changes` | Change analyst | Sonnet 5.5 | Matching a follow-up to the agreed scope by meaning |
| `learn_estimation_rules` | Learner | Sonnet 5.5 | Rules from hour edits; bad rules would skew every later estimate |

These are the blueprint's starting choices. Each is to be confirmed with 20–30 eval cases (quality, cost, latency) before the write-up.

## File format

Settings go between two `---` lines at the top. Everything below them is the system prompt.

| Setting | Meaning |
| --- | --- |
| `model` | The Claude model that runs the prompt |
| `fallback_model` | Optional. Tried once when `model` still fails after the SDK's retries. It must accept the same `effort` and `thinking` |
| `effort` | `low`, `medium`, `high`, `xhigh` or `max` |
| `thinking` | `adaptive` (the model decides) on every model. To turn thinking off: `disabled` on Haiku 5.5, `between_tools` on Sonnet 5.5. Opus 5.5 always thinks |
| `max_tokens` | Room for the reply, thinking included. A reply cut off here gets one retry with twice the room |
| `changelog` | Why this version exists |

## Rules

- **Never edit a released version.** Copy it to the next number, change the copy, and say why in `changelog`. Each agent names the version it runs (`PROMPT, VERSION = ...` at the top of its file).
- **Transcripts are data, not instructions.** They go inside tags that a speaker can't close (`app/text.py`), and every prompt says to treat them as data.
- **No numbers from the writer.** Code renders the proposal's prices and hours from the scope.
