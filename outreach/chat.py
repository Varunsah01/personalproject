"""Chat bridge — routes dashboard questions to Claude CLI with row context.

Invoked by the dashboard API route via subprocess. Reads the user message
from stdin, loads row context from tracker.csv, classifies intent to pick
relevant agent knowledge, builds a system prompt, and streams Claude CLI
output to stdout.

Usage (from the dashboard API route)::

    echo "Why did Researcher pick this?" | python3 outreach/chat.py --row-id <uuid>

The first line of stdout is metadata: ``__agent__:<agent_name>``
Everything after that is the streamed response text.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from outreach.lib import tracker  # noqa: E402

_TRACKER_PATH = Path("outreach/data/tracker.csv")
_PRINCIPLES_PATH = Path("outreach/prompts/principles.md")
_AGENTS_DIR = Path(".claude/agents")

# ---------------------------------------------------------------------------
# Intent classification
# ---------------------------------------------------------------------------

_INTENT_KEYWORDS: list[tuple[str, list[str]]] = [
    ("role_researcher", ["research", "company", "role", "jd", "job description",
                         "tier", "why picked", "opening", "job board"]),
    ("people_finder",   ["person", "contact", "who", "hiring manager", "founder",
                         "team lead", "linkedin profile"]),
    ("channel_finder",  ["email", "channel", "linkedin", "hunter", "email address",
                         "contact method"]),
    ("follow_up_writer", ["follow up", "followup", "follow-up", "bump", "nudge"]),
    ("manager",         ["score", "quality", "approve", "review", "rubric",
                         "specificity", "voice dimension", "risk dimension"]),
    ("cv_customizer",   ["cv", "resume", "customize", "tailor"]),
    # message_writer last — its keywords ("draft", "message") are generic and
    # would otherwise shadow more specific intents like "score this draft"
    ("message_writer",  ["rewrite", "hook", "subject line", "write", "shorten",
                         "tone", "body", "bridge", "draft"]),
]


def classify_intent(message: str) -> str:
    """Return the agent name whose knowledge is most relevant to *message*."""
    lower = message.lower()
    best_agent = "manager"
    best_hits = 0
    for agent, keywords in _INTENT_KEYWORDS:
        hits = sum(1 for kw in keywords if kw in lower)
        if hits > best_hits:
            best_hits = hits
            best_agent = agent
    return best_agent


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------

def _load_row(row_id: str) -> tracker.Row | None:
    """Find a single row by ID."""
    for row in tracker.read_all(_TRACKER_PATH):
        if row.id == row_id:
            return row
    return None


def _format_row_context(row: tracker.Row) -> str:
    """Format row fields as structured context for the system prompt."""
    fields = [
        ("id", row.id),
        ("company", row.company),
        ("role_url", row.role_url),
        ("role_title", row.role_title),
        ("role_tier", row.role_tier),
        ("person_name", row.person_name),
        ("person_title", row.person_title),
        ("person_linkedin", row.person_linkedin),
        ("person_country", row.person_country),
        ("relationship_type", row.relationship_type),
        ("email", row.email),
        ("email_confidence", row.email_confidence),
        ("linkedin_only", row.linkedin_only),
        ("hook", row.hook),
        ("subject", row.subject),
        ("body_path", row.body_path),
        ("status", row.status),
        ("assigned_inbox", row.assigned_inbox),
        ("sent_at_utc", row.sent_at_utc),
        ("replied", row.replied),
        ("notes", row.notes),
        ("last_updated", row.last_updated),
    ]
    lines = ["## Outreach row context\n"]
    for label, value in fields:
        if value:
            lines.append(f"- **{label}:** {value}")
    return "\n".join(lines)


def _load_draft(row: tracker.Row) -> str:
    """Load draft file content if body_path is set and exists."""
    if not row.body_path:
        return ""
    draft_path = Path(row.body_path)
    if not draft_path.exists():
        return ""
    try:
        text = draft_path.read_text(encoding="utf-8")
        return f"\n## Draft content (from {row.body_path})\n\n```\n{text}\n```"
    except OSError:
        return ""


def _load_principles() -> str:
    """Load the voice & craft rules."""
    if not _PRINCIPLES_PATH.exists():
        return ""
    try:
        text = _PRINCIPLES_PATH.read_text(encoding="utf-8")
        return f"\n## Voice & craft rules (principles.md)\n\n{text}"
    except OSError:
        return ""


def _load_agent_knowledge(agent_name: str) -> str:
    """Load the relevant agent's markdown file for domain knowledge."""
    agent_path = _AGENTS_DIR / f"{agent_name}.md"
    if not agent_path.exists():
        return ""
    try:
        text = agent_path.read_text(encoding="utf-8")
        return f"\n## Agent knowledge ({agent_name})\n\n{text}"
    except OSError:
        return ""


def _load_history(history_file: str | None) -> str:
    """Load recent chat history from a JSON file."""
    if not history_file:
        return ""
    path = Path(history_file)
    if not path.exists():
        return ""
    try:
        messages = json.loads(path.read_text(encoding="utf-8"))
        if not messages:
            return ""
        lines = ["\n## Recent conversation history\n"]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            lines.append(f"**{role}:** {content}\n")
        return "\n".join(lines)
    except (json.JSONDecodeError, OSError):
        return ""


def build_system_prompt(
    row: tracker.Row,
    agent_name: str,
    history_file: str | None = None,
) -> str:
    """Assemble the full system prompt from row context + agent knowledge."""
    parts = [
        "You are a chat assistant for Varun's cold-outreach pipeline. "
        "You answer questions about a specific outreach row and provide "
        "advisory recommendations.\n\n"
        "## Hard constraints\n\n"
        "- You NEVER modify tracker.csv, draft files, or any files on disk.\n"
        "- If asked to rewrite a draft, hook, or subject line, show the "
        "rewritten text in your response only. Do NOT save it anywhere.\n"
        "- All your outputs are advisory. The user will decide whether to act.\n"
        "- Never fabricate facts about the company, person, or Varun's background.\n"
        "- If you don't know something, say so.\n",
        _format_row_context(row),
        _load_draft(row),
        _load_principles(),
        _load_agent_knowledge(agent_name),
        _load_history(history_file),
    ]
    return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Claude CLI invocation
# ---------------------------------------------------------------------------

def stream_claude(system_prompt: str, message: str) -> int:
    """Spawn claude --print and stream its output to stdout.

    Returns the process exit code.
    """
    claude_path = shutil.which("claude")
    if not claude_path:
        print("error: claude CLI not found on PATH", file=sys.stderr)
        return 1

    cmd = [
        claude_path,
        "--print",
        "--model", "sonnet",
        "--system-prompt", system_prompt,
        "--bare",
        message,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(_project_root),
    )

    try:
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    except BrokenPipeError:
        pass
    finally:
        proc.wait()

    if proc.returncode != 0:
        err = proc.stderr.read().decode("utf-8", errors="replace")
        if err.strip():
            print(f"\n[claude error: {err.strip()}]", file=sys.stderr)

    return proc.returncode


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chat bridge — routes dashboard questions to Claude CLI"
    )
    parser.add_argument("--row-id", required=True, help="UUID of the tracker row")
    parser.add_argument(
        "--history-file",
        default=None,
        help="Path to a JSON file with recent chat messages",
    )
    args = parser.parse_args()

    # Read message from stdin
    message = sys.stdin.read().strip()
    if not message:
        print("error: no message provided on stdin", file=sys.stderr)
        sys.exit(1)

    # Load row
    row = _load_row(args.row_id)
    if row is None:
        print(f"error: row {args.row_id} not found in tracker", file=sys.stderr)
        sys.exit(1)

    # Classify and build prompt
    agent_name = classify_intent(message)
    system_prompt = build_system_prompt(row, agent_name, args.history_file)

    # Emit metadata line (parsed and stripped by the API route)
    sys.stdout.write(f"__agent__:{agent_name}\n")
    sys.stdout.flush()

    # Stream response
    exit_code = stream_claude(system_prompt, message)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
