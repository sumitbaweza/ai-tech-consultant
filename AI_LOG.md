# AI log

I used one AI coding tool for the whole build: **Claude Code** (desktop app, Claude Opus 5.5). Every chat thread is submitted with the repo. I planned in a Claude chat first and saved the decisions in `CLAUDE.md`, which Claude Code reads at the start of every session.

## My 5 most useful prompts

1. **The standing instructions in `CLAUDE.md`**, read by every session:
   > Before building a new component, give me the plan in 3–5 bullets. Wait for my OK if it changes the architecture above. Make small, reviewable changes. After each one, suggest a commit message.

   **Why it helped:** every feature arrived as a short plan I could change, then as one commit I could review and explain. It also carried my hard rules (one Claude wrapper, no temperature, Task 1 only through its API) into every session.

2. **"Read CLAUDE.md and docs/blueprint.md Start to create the project"**

   **Why it helped:** one prompt turned a day of planning into a build plan. Claude checked my machine (Docker and uv were missing) and the real sitemap (1,700 URLs, all with dates), and gave me a nine-step Day 1 plan with a commit for each step.

3. **"Set up evals first"**, before writing any eval questions.

   **Why it helped:** I got the case format, a free check that every expected article is crawled and active, and a runner that adds up cost per case from the ledger. Every case I add is now scored the same way.

4. **"Yes, fix it here"**, after a crawl saved three pages twice.

   **Why it helped:** instead of a quick patch, Claude matched the stalls to Windows sleep events and found the cause. A sleeping laptop froze Docker, so a page's claim looked stale on waking and a second worker took it. The fix is that only the worker still holding the claim may save. It's a real bug I can now explain in the review.

5. **"do rest all step together"**, for the last two steps of the Brain's screens.

   **Why it helped:** once each step had the same shape (plan, code, tests, browser check), batching them saved a round of approvals. The browser check still caught two problems before commit: screen links that showed before sign-in, and labels cut off in a narrow window.

## One time I rejected the AI's answer, and why

For the evals, Claude recommended building and running the full eval sets: 30 Brain questions, 30 emails and 20 scope cases, each on Haiku and Sonnet, for about $10 in API calls. I said no, because I had no API credit left. Instead, EVALS.md reports what was measured on a few live calls, calculates the costs, and marks every accuracy and speed number as not run, so nothing in it is made up.
