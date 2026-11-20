# Agent transcripts

- **`main-session.md`: read this one.** The main Claude Code session (Claude Opus 5.5)
  as readable Markdown: user messages, Claude's replies, and each tool call with its
  output folded (long outputs truncated, system reminders omitted).
- `main-session.jsonl`: the same session as exported from the Claude desktop app (raw,
  one JSON record per event). `main-session.md` is generated from it:
  `python transcripts/to_markdown.py transcripts/main-session.jsonl transcripts/main-session.md`

The export starts on Nov 19 at 17:18 local time (22:18 UTC), when data collection was
already running; the earlier work (project scaffold, API exploration, first version of the
collector) happened before that point in the conversation and is not part of this export.

Redacted before committing: the author's email address and local username (home-directory
paths shown as `~`). Nothing else was changed.
