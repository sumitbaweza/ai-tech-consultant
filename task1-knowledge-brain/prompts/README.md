# Prompts

Each prompt is a folder with one file per version: `answer/v1.md`, `answer/v2.md`, and so on.
`app/llm.py` loads a prompt by name and version, and every row in `brain.llm_calls` records
which version ran. That's how an eval result or a cost ties back to the exact prompt text.

## File format

Settings go between two `---` lines at the top. Everything below them is the system prompt.

```
---
model: claude-sonnet-5-5
fallback_model: claude-haiku-5-5
effort: medium
thinking: adaptive
max_tokens: 2000
changelog: One line on what changed from the previous version.
---
You answer questions about ...
```

| Setting | Meaning |
| --- | --- |
| `model` | The Claude model that runs the prompt |
| `fallback_model` | Optional. Tried once when `model` still fails after the SDK's retries (overloaded, rate-limited, down). It must accept the same `effort` and `thinking` |
| `effort` | `low`, `medium`, `high`, `xhigh` or `max` |
| `thinking` | `adaptive` (the model decides) on every model. To turn thinking off: `disabled` on Haiku 5.5, `between_tools` on Sonnet 5.5, both only at effort `high` or lower. Opus 5.5 always thinks |
| `max_tokens` | Room for the reply, thinking included. A reply cut off here gets one retry with twice the room |
| `changelog` | Why this version exists |

An unknown or misspelt setting is an error, so a typo can't silently change behaviour.

## Rules

- **Never edit a released version.** Copy it to the next number, change the copy, and say why in `changelog`. Callers pick the version they run, so old and new can be compared on the same eval set.
- **Text from outside is data, not instructions.** Crawled pages, emails and transcripts go inside tags, and the prompt says to treat them as data.
