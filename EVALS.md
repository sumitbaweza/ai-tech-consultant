# Evals

> **Read this first: the full eval runs have not been done.** I ran out of API credit before running them. Below are three kinds of numbers, each labelled:
> **measured** numbers come from real calls logged in `llm_calls` during live checks;
> **estimated** costs are calculations (input size × price + assumed output × price);
> **not run** marks every accuracy and speed number that needs the full runs.
> No accuracy number in this file is invented.

## What I measured for real

**Task 1, a live check of three Brain calls ($0.029 in total)**

| Request | Answered by | Cost (measured) |
| --- | --- | --- |
| "How do I manually approve new member signups?" | Haiku 5.5 (router) | $0.0007 |
| "Why choose Stripe over PayPal?" | Sonnet 5.5 (router) | $0.0145 |
| Capability check: approval before a listing goes live | Sonnet 5.5: "yes", with the setting and its article | $0.0139 |

The router's threshold also comes from measured data. Questions an article answers directly scored 0.60 to 0.66 in search; vaguer or off-topic ones scored 0.32 to 0.57. So Haiku answers at 0.60 and above.

**Task 2, a live run on real Gmail and the demo emails (18 Claude calls, $0.08, so $0.0044 a call)**

- 5 real emails were classified and drafted by the worker on its own.
- A demo email with an injection hidden in an HTML comment ("give them 6 months free") was flagged by both the code guard and the classifier, and the draft ignored the instruction.
- A demo email with three logo questions: the Brain answered one with citations. The draft answered that one and handed the other two to a person instead of guessing. One of the two was a Brain miss (see DESIGN.md, "What breaks").
- One edit in the browser (7% of characters changed) became a learned rule, playbook v1, within a minute.

**Task 3, live runs on the stand-in transcripts.** These found three real problems, all fixed and now covered by tests: a database session bug (MissingGreenlet), an empty Brain making every requirement custom work, and a follow-up meeting whose dropped blog stayed in scope. I didn't record each run's total cost for this write-up; the run screen shows the cost by agent.

**Automated checks: 372 tests, all passing** (Task 1: 223, Task 2: 80, Task 3: 69). They call no model, so they check the code that keeps the models honest, every time:

- **Task 1:**
  - invented citation ids are dropped;
  - a question with nothing close never reaches Claude;
  - an archived article is never cited;
  - a broken sitemap never archives a URL;
  - repeated questions count on one expert task.
- **Task 2:** every never-auto-send reason, earning auto-send at X, reset on an edit, the undo window, revoking after a bad spot check, playbook rollback, and the Brain-down path.
- **Task 3:**
  - a requirement whose quote isn't in the transcript is dropped;
  - invented ids from the merger are ignored;
  - an injection attempt in a transcript is treated as data;
  - a draft proposal can't be exported before approval.

## The eval sets

| Task | Cases | What a case checks | Models compared | Built |
| --- | --- | --- | --- | --- |
| 1 Brain | 30: 15 how-to, 5 answered only by a screenshot, 5 the help center can't answer, 3 follow-ups, 2 about an article archived during the test | Right article in the top 5, a citation of an expected article, an honest "I don't know", latency, cost | Router vs Haiku only vs Sonnet only (`--model`) | Runner built (`evals/run.py`); 3 of 30 cases written and checked against the crawl |
| 2 Inbox | 30 of the 50 demo emails (the other 20 kept for the learning replay) | Intent and area correct, facts correct, risky email never auto-sent, injection resisted | Classify: Haiku vs Sonnet. Draft: Sonnet vs Haiku | Emails written; runner not built |
| 3 Scope | 20: the 3 transcripts plus short snippets | Requirements found, requirements invented, setting vs custom, hours within 30% of mine, change labels in meeting 2 | Extractor: Haiku vs Sonnet. Reviewer: Opus vs Sonnet | Transcripts written; runner not built |

Answer quality was to be graded by Opus 5.5 as the judge, against each case's expected answer, with 10 grades checked by hand.

## Results by decision

Accuracy and speed are **not run**. Costs per case use measured input sizes: the prompt files, the 50 demo emails (52 tokens on average) and the transcripts (544 to 1,542 tokens). Output sizes are assumed. The measured Task 1 calls cost about 1.4 times a plain token estimate, because of thinking tokens, so read the estimated Sonnet and Opus costs as lower bounds.

**Decision 1: who answers Brain questions?**

| Setup | Accuracy | Cost per answer | 30 cases | Latency p50 / p95 |
| --- | --- | --- | --- | --- |
| Haiku 5.5 only | not run | $0.0007 (measured) | $0.02 | not run |
| Sonnet 5.5 only | not run | $0.0145 (measured) | $0.44 | not run |
| Router (shipped) | not run | between the two | $0.23 if half go to Haiku | not run |

**Decision 2: who classifies email?** (input: about 1,000 tokens; output: 200)

| Model | Category accuracy | Cost per email | 30 emails | Latency |
| --- | --- | --- | --- | --- |
| Haiku 5.5 (shipped) | not run | $0.0002 (estimated) | $0.006 | not run |
| Sonnet 5.5 | not run | $0.004 (estimated) | $0.12 | not run |

**Decision 3: who drafts replies?** (input: about 2,100 tokens with voice guide, rules, examples and facts; output: 300)

| Model | Judge score, unedited rate | Cost per draft | 30 drafts | Latency |
| --- | --- | --- | --- | --- |
| Sonnet 5.5 (shipped) | not run | $0.0072 (estimated) | $0.22 | not run |
| Haiku 5.5 | not run | $0.0004 (estimated) | $0.01 | not run |

**Decision 4: who extracts requirements, and who reviews the estimate?**

| Agent | Model | Quality | Cost per meeting | 20 cases | Latency |
| --- | --- | --- | --- | --- | --- |
| Extractor (input 2,300, output 1,000) | Haiku 5.5 (shipped) | not run | $0.0007 (estimated) | $0.01 | not run |
| | Sonnet 5.5 | not run | $0.015 (estimated) | $0.29 | not run |
| Reviewer (input 4,200, output 600) | Opus 5.5 (shipped) | not run | $0.029 (estimated) | $0.58 | not run |
| | Sonnet 5.5 | not run | $0.014 (estimated) | $0.28 | not run |

**What the full eval would cost:** about $1.40 for Task 1 (three setups, each run twice to see the spread, since answers vary between runs), $0.70 for Task 2 (with the Brain's facts for 30 emails), $1.20 for Task 3's two compared agents, and $1.10 for 160 Opus judgments at about $0.007 each. That's about $4.40, or about $6 with thinking tokens.

## How to fill in the real numbers

1. Add credit to the Anthropic key in `.env`, then `make up`.
2. Task 1: write the remaining 27 cases in `task1-knowledge-brain/evals/cases.json`, check them for free with `make eval-check`, then run `make eval` three times: plain, with `--model haiku`, and with `--model sonnet`. Each run writes `evals/results/<run id>.json`, with accuracy, p50 and p95 latency, and cost per case from `llm_calls`.
3. `make eval-experts` re-asks each question an expert answered, and reports how many the Brain answers now: the "answered this week, failed last week" number.
4. Tasks 2 and 3 need their runners: the same pattern, reading their own `llm_calls` tables for cost and speed.

---

**About these numbers:** the cost figures above are demo calculations, not results of eval runs. I had no funds left for API calls to run the full evals, so I measured what I could on a few live calls, calculated the rest, and marked every accuracy and speed number as not run.
