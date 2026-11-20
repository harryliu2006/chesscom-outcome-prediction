"""Render a Claude Code session export (.jsonl) as readable Markdown.

Keeps the conversation: user messages, assistant replies, and each tool call with its
output in a collapsible block (long outputs truncated). Drops the app's bookkeeping
records (API request blobs, file snapshots, hook records) and system reminders.

Usage:  python transcripts/to_markdown.py transcripts/main-session.jsonl transcripts/main-session.md
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime

MAX_OUTPUT = 2500     # characters of tool output shown
MAX_INPUT_LINES = 60  # lines of a written file / long command shown
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
HIDDEN_TOOLS = {"mcp__ccd_turn__update_plan"}  # task-list bookkeeping


def clean(text: str) -> str:
    return REMINDER.sub("", text).strip()


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    text = text.rstrip()
    return text if len(text) <= limit else text[:limit] + f"\n… [{len(text) - limit:,} more characters]"


def trim_lines(text: str, n: int = MAX_INPUT_LINES) -> str:
    lines = text.splitlines()
    return text if len(lines) <= n else "\n".join(lines[:n]) + f"\n… [{len(lines) - n} more lines]"


def fence(text: str, lang: str = "") -> str:
    ticks = "````" if "```" in text else "```"
    return f"{ticks}{lang}\n{text}\n{ticks}"


def result_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if b.get("type") == "text":
            parts.append(b["text"])
        elif b.get("type") == "image":
            parts.append("[image]")
    return "\n".join(parts)


def tool_summary(name: str, inp: dict) -> tuple[str, str]:
    """(one-line summary, body) for a tool call."""
    if name == "Bash":
        return f"Bash: {inp.get('description', '')}", fence(trim_lines(inp.get("command", "")), "bash")
    if name == "Read":
        return f"Read `{inp.get('file_path', '')}`", ""
    if name == "Write":
        return f"Write `{inp.get('file_path', '')}`", fence(trim_lines(inp.get("content", "")))
    if name == "Edit":
        body = ("Replace:\n" + fence(trim_lines(inp.get("old_string", ""), 25)) +
                "\nWith:\n" + fence(trim_lines(inp.get("new_string", ""), 40)))
        return f"Edit `{inp.get('file_path', '')}`", body
    if name == "mcp__ccd_turn__wrap_up":
        body = inp.get("recap", "")
        if inp.get("ask"):
            body += f"\n\nQuestion: {inp['ask']}"
        return "Wrap-up", body
    short = name.split("__")[-1]
    return short, fence(truncate(json.dumps(inp, indent=1), 1500), "json")


def render(rows: list[dict], source: str = "the .jsonl export") -> str:
    out = ["# Session transcript", "",
           "Claude Code session (Claude Opus 5.5) that built this project. Rendered from "
           f"`{source}` by `to_markdown.py`: tool calls and their output are folded; "
           "long outputs are truncated; system reminders are omitted.", ""]
    results = {}
    for r in rows:
        if r.get("type") == "user" and isinstance(r["message"].get("content"), list):
            for b in r["message"]["content"]:
                if b.get("type") == "tool_result":
                    results[b["tool_use_id"]] = result_text(b.get("content"))

    speaker = None
    for r in rows:
        if r.get("isSidechain") or r.get("type") not in ("user", "assistant"):
            continue
        msg = r["message"]
        content = msg.get("content")
        ts = r.get("timestamp", "")
        when = datetime.fromisoformat(ts.replace("Z", "+00:00")).strftime("%b %d %H:%M UTC") if ts else ""

        if r["type"] == "user":
            texts = [content] if isinstance(content, str) else [b["text"] for b in content if b.get("type") == "text"]
            for t in texts:
                if r.get("isCompactSummary"):
                    out += ["---", f"*Earlier conversation, summarised by the app when the context filled up ({when}):*",
                            "", "<details><summary>Summary</summary>", "", clean(t), "", "</details>", ""]
                    continue
                t = clean(t)
                if not t:
                    continue
                if t.startswith("[SYSTEM NOTIFICATION") or "<task-notification>" in t:
                    out += ["*(background task finished)*", ""]
                elif t.startswith("[Request interrupted"):
                    out += ["*(user interrupted the tool call)*", ""]
                else:
                    out += ["---", f"## User · {when}", "", t, ""]
                    speaker = "user"
            continue

        if speaker != "claude" and any(b.get("type") in ("text", "tool_use") for b in content):
            out += ["### Claude", ""]
            speaker = "claude"
        for b in content:
            if b.get("type") == "text" and b["text"].strip():
                out += [b["text"].strip(), ""]
            elif b.get("type") == "tool_use" and b["name"] not in HIDDEN_TOOLS:
                summary, body = tool_summary(b["name"], b.get("input", {}))
                res = results.get(b["id"], "")
                out += [f"<details><summary>🔧 {summary}</summary>", ""]
                if body:
                    out += [body, ""]
                if res.strip():
                    out += ["Output:", fence(truncate(res)), ""]
                out += ["</details>", ""]
    return "\n".join(out)


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    rows = [json.loads(line) for line in open(src)]
    with open(dst, "w") as f:
        f.write(render(rows, source=src.split("/")[-1]))


if __name__ == "__main__":
    main()
