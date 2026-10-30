# Planning chat summary (Claude, Oct 29–30, 2026)

What happened in the planning conversation before building started. Keep this file: the brief asks to see AI conversations, so keep the planning chat as well, in case they ask how the design was reached.

## 1. Reading the brief (Oct 29)

- I asked Claude to read `test 1.pdf` and `test 2.pdf` in this folder and brief me.
- `test 1.pdf` is the main 8-page brief, "AI Tech Consultant": three connected tasks, the rules, the deliverables and the scoring. `test 2.pdf` is a 3-page feature spec for Tasks 1 and 2 only.
- Things Claude flagged:
  - **The PDFs disagree on two points.**
    - Grouping: test 1 groups emails by intent and test 2 by product area. Resolution: label every email with both (intent × product area).
    - Auto-send threshold: test 1 says 20 unedited sends in a row, test 2 says X configurable. Resolution: make it configurable, defaulting to 20.
  - **Probable typo:** test 2 lists "Pages per 1 hour" and "Pages per hour". The first is probably meant to be pages per minute.
  - **The clock:** the 4 days start when the brief is opened.
  - **The pass rule:** you need 80+ overall **and** at least 60% in every part, so one weak part fails the whole submission.
- The summary was saved as `docs/capstone-brief.md`.

## 2. Blueprint (Oct 30)

- Claude produced a full blueprint doc: https://claude.ai/artifact/PEaKLyj4hnQPUJJiJrQiy3 (copied here as `docs/blueprint.md`).
- It covers:
  - the architecture diagram and the Task 3 agent-team diagram
  - the design of each task
  - model routing with current prices
  - prompts, self-improvement and evals
  - the tech stack
  - a ~35-hour learning list and a setup checklist
  - a 4-day checklist
  - the repo layout
  - the order to cut features in
  - a 10-minute demo script
- Claude model prices were checked against Claude's pricing docs on Oct 30, 2026.
- The sitemap at `support.brilliantdirectories.com/support/sitemap.xml` could not be fetched from the cloud session, so its URL count and lastmod dates are still unknown.

## 3. Key ideas worth remembering

- Design and evals are worth 40 of the 100 points, so build a thin end-to-end version by Day 2 and spend the rest on depth, evals and writing.
- Route every Claude call through one wrapper with a versioned prompt, a JSON schema, retries and a cost log (`llm_calls`). That ledger feeds every number in EVALS.md.
- The Task 3 fit checker is where the client saves money: a request that an existing setting handles costs 0.5 to 1 hour of configuration instead of custom code.
- **Needs nobody wrote down:**
  - follow-up tracking in the inbox
  - a never-auto-send list (refunds, legal, angry customers, personal data, prompt injection, Brain "don't know")
  - a 5-minute undo window on auto-sent emails
  - the expert loop counted as weekly improvement
- **For reviewers:** ship a crawl seed snapshot and a Gmail fixtures mode, because reviewers won't wait hours for a crawl and can't access your Gmail.
- **Practice separately:** use a scratch repo before the clock starts, then start the real repo fresh so the commits and AI chat log tell an honest 4-day story.

## 4. Open questions

- Has the 4-day clock started?
- TypeScript or Python for Task 2? TypeScript earns a 10% bonus.
- How many URLs does the sitemap have, and does it have lastmod dates? Check on Day 1.
- Is `test 2.pdf` part of the same assignment or a variant?
