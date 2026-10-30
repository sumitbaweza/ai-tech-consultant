# Capstone Brief: AI Tech Consultant (summary of test 1.pdf + test 2.pdf)

Source: two PDFs in the user's "interview project" folder.
- **test 1.pdf** (8 pages): the main capstone brief, "AI Tech Consultant", dated Oct 14, 2026, from BusinessLabs / Anthropic Applied AI.
- **test 2.pdf** (3 pages): a detailed feature spec for Task 1 and Task 2 only. It adds concrete UI and behaviour requirements. Task 3 has no extra spec.

## The idea
Software prices fell about 90% (a $2,000 job now sells for about $200), so a tech company must do 10–20x more work per person. Writing code is now the fast part. The slow part is knowing the product, answering customer emails, and turning meetings into scopes of work. Build one AI Tech Consultant that does all three, gets better every week from human feedback, and works for any software or service business (plug in your own help center, inbox and meetings).

## Three tasks (one system)
Tasks 2 and 3 must call Task 1 **through its API**, not copy its code.

| Task | What | Language |
|---|---|---|
| 1. Knowledge Brain | RAG memory + chatbot over a public help center, refreshed daily | Python |
| 2. Inbox Agent | Classifies emails, drafts replies via Task 1, learns from human edits, earns auto-send | TypeScript (Python allowed if you don't know TS; never mix inside a task; knowing both = +10%) |
| 3. Meeting to Scope of Work | Multi-agent team: transcripts → scope, estimate, proposal; handles follow-up meetings | Python |

### Task 1: Knowledge Brain
Data source (same for all candidates): Brilliant Directories Solution Center, sitemap at `support.brilliantdirectories.com/support/sitemap.xml`. Structure: category/section → folder (topic) → article; many articles have screenshots that carry the answer.

From test 1:
- Polite first crawl: batching, rate limiting, resumable if it stops halfway.
- Understand images (multimodal): know what each screenshot shows and which article it belongs to.
- Daily freshness: diff the sitemap; add/update/remove only what changed; removed articles must stop appearing in answers.
- Chatbot: correct answers, cite source articles, say "I don't know" honestly.
- Clean, documented API usable by another developer from the docs alone.
- Unanswerable question → creates a task for a human expert → expert's answer flows back into memory.

Extra from test 2:
- User authentication / account connection.
- Add one or many sitemap URLs; auto-detect nested sitemaps.
- Pre-processing summary: total sitemaps, total URLs, first URL, last URL (per nested sitemap and final total). User must confirm before ingestion starts.
- ETA: pages per minute (PDF says "per 1 hour", likely a typo), per hour, per day, expected completion time. Live progress while processing.
- Auto embeddings; chat uses retrieved content + conversation history without detailed prompts.
- Recheck schedule: daily, weekly, every 15 days, every 30 days. New URLs added; updated pages re-crawled and re-embedded.
- Dashboard: processed, embedded, failed, pending, duplicate URLs skipped, last sync time.
- Retention rules for URLs missing from sitemap: archive immediately / after 2 / after 15 / after 30 consecutive missing checks. Never delete: move from Active KB to Archived KB.
- Duplicate URL across sitemaps: flag it and list every sitemap it appears in.
- Change history for content changes.
- HTTP status handling: 301 (follow new URL, update records, re-index), 404, Active.

### Task 2: Inbox Agent
From test 1:
- Understand what each email wants; get facts from Task 1 API; draft in company voice.
- Human can send as-is, edit, or write own. Agent learns from each case. You decide storage of learnings and how to prove it improves, not degrades.
- After 20 unchanged sends in a row of "the same kind", that kind may auto-send. You define "same kind" and "unchanged", and how autonomy is revoked when quality drops.
- No dataset: write 50+ realistic customer emails yourself (how-to, new project, refund, complaint, quote, follow-up, etc.). Hidden emails will be tested.

Extra from test 2:
- Gmail OAuth; fetch real Gmail; Gmail-like UI (Inbox, Drafts, Sent, Spam, Trash in familiar places; profile/settings icon top-right).
- Auto-classify every email into configurable categories, e.g. Filters, Home Page, Domain Management, Header, Main Menu, Footer, General Settings, Design Settings, Managers (Brilliant Directories admin areas).
- Draft for every email; send directly or edit then send.
- Learn from the diff between AI draft and final sent email.
- Per-category confidence score; X consecutive unedited approvals (configurable) → auto-send for that category; any edit resets the count and returns to manual review.

Note: test 1 frames "kind" by intent (refund, how-to, quote); test 2 frames categories by product area. A two-axis label (intent × product area) satisfies both.

### Task 3: Meeting to Scope of Work
- Team of agents (you pick roles and handoffs). Reads one or more transcripts, asks questions a senior consultant would ask.
- Outputs: scope of work, task list with hour estimates, client-ready proposal.
- Must check Task 1 for existing product settings that solve a request (cheaper than custom code).
- Second meeting: show what changed, what is new, and what is out of scope (quote extra). Update scope, don't restart.
- Long transcripts must be handled well. Human approval before anything reaches the client.
- No dataset: role-play and record 3+ client meetings with a friend; one must be a follow-up that changes the first.

## Rules
- Solo, 4 days from the moment the brief is opened.
- Modern Python (async, type hints, FastAPI). TypeScript strict, typed end to end, Node.js/Next.js.
- Claude at the centre. Other models allowed only with numbers. Every model choice (even between Claude models) justified with 20–30 evals.
- Use one AI coding tool only (Claude Code, Cursor, Antigravity or Codex); submit all chat threads end to end.
- Not allowed: group work, copying repos, code you can't explain line by line.

## Deliverables
| File | Length | Must show |
|---|---|---|
| GitHub repo | any | One folder per task; whole project runs with one command; commits spread over 4 days |
| README.md | ≤1 page | What it does, how to run |
| DESIGN.md | 3–5 pages | Problem in own words, features chosen/dropped and why, one diagram of how tasks connect, model choices, what breaks, what's next |
| EVALS.md | 2–3 pages | 20+ test cases per task, models compared, quality/cost/speed for each |
| AI_LOG.md | ≤1 page | 5 most useful prompts, one time you rejected the AI's answer and why |
| Demo video | ≤10 min | Face for first 30 s, all three tasks working together, one failure case |

Writing style: plain English, why before what, numbers not adjectives, honest about failures, 6–10 pages total.

## Scoring (100)
| Part | Max | Min (60%) |
|---|---|---|
| Problem thinking & feature ideas | 15 | 9 |
| Design: UI/UX, architecture, agent design | 20 | 12 |
| Code quality | 15 | 9 |
| Prompt design & self-improvement (versioned prompts, schemas, measurable + undoable feedback) | 15 | 9 |
| Evals: quality, cost, speed, reliability, safety | 20 | 12 |
| Writing & demo | 15 | 9 |

**Fit** = 80+ total AND every part ≥60%. Otherwise Not a fit.
Also: 30-minute live review (explain code, make one small change live; failing to explain = fail) and 10–30 hidden test cases (questions, emails, transcripts).
