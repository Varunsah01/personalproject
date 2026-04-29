"""Manager CLI — score outreach drafts against the quality rubric.

Callable from the dashboard, review.py, or command line:

    python -m outreach.manager --rescore <row_id>
    python -m outreach.manager --explain <row_id>
    python -m outreach.manager --batch-review

Uses the Anthropic API (ANTHROPIC_API_KEY in .env) to score drafts.
Writes scores to tracker.csv notes field.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from outreach.lib import tracker  # noqa: E402

logger = logging.getLogger(__name__)

TRACKER_PATH = _PROJECT_ROOT / "outreach" / "data" / "tracker.csv"

_DEFAULT_MODEL = "claude-sonnet-4-6"

_SCORE_PATTERN = re.compile(
    r"quality:\s*(\d)/(\d)/(\d)/(\d)/(\d)\s+avg=(\d+(?:\.\d+)?)"
)

_SCORING_PROMPT = """\
You are a cold-outreach quality reviewer. Score this draft email on 5 dimensions, 1-5 each.

## Rubric

| Dimension | 1 (fail) | 5 (excellent) |
|---|---|---|
| Specificity | Generic — could be sent to anyone | References something only this person/company would care about |
| Voice | Sounds like a LinkedIn bot | Natural, confident, matches a founder/operator tone |
| Ask | Vague or high-friction ("let me know") | Clear, low-commitment, proportionate ("15 min call next week?") |
| Length | Over 120 words or padded | Under 120 words; every sentence earns its place |
| Risk | Would embarrass if leaked; fabricated facts | Fully verifiable, professional, no downside |

## Context

Company: {company}
Role: {role_title} (Tier: {role_tier})
Recipient: {person_name}
Hook used: {hook}

## Draft

{draft_body}

## Instructions

Score each dimension 1-5. Provide a one-line feedback for each.
Respond with ONLY valid JSON, no markdown fences:

{{"specificity": N, "voice": N, "ask": N, "length": N, "risk": N, "feedback": {{"specificity": "...", "voice": "...", "ask": "...", "length": "...", "risk": "..."}}}}
"""

_EXPLAIN_PROMPT = """\
You previously scored this outreach draft. Explain each dimension score in 2-3 sentences.

## Rubric

| Dimension | 1 (fail) | 5 (excellent) |
|---|---|---|
| Specificity | Generic — could be sent to anyone | References something only this person/company would care about |
| Voice | Sounds like a LinkedIn bot | Natural, confident, matches a founder/operator tone |
| Ask | Vague or high-friction ("let me know") | Clear, low-commitment, proportionate ("15 min call next week?") |
| Length | Over 120 words or padded | Under 120 words; every sentence earns its place |
| Risk | Would embarrass if leaked; fabricated facts | Fully verifiable, professional, no downside |

## Context

Company: {company}
Role: {role_title}
Recipient: {person_name}

## Existing scores

Specificity={specificity}, Voice={voice}, Ask={ask}, Length={length}, Risk={risk}, Avg={avg}

## Draft

{draft_body}

## Instructions

For each dimension, explain in 2-3 sentences why the draft earned that score.
Respond with ONLY valid JSON, no markdown fences:

{{"specificity": "...", "voice": "...", "ask": "...", "length": "...", "risk": "..."}}
"""


def _get_client():
    """Lazy-init the Anthropic client."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        # Try loading from .env
        env_path = _PROJECT_ROOT / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
                    break
    if not api_key:
        logger.error("ANTHROPIC_API_KEY not set — cannot score drafts")
        sys.exit(1)

    import anthropic
    return anthropic.Anthropic(api_key=api_key)


def _get_model() -> str:
    return os.getenv("RELEVANCE_MODEL", _DEFAULT_MODEL)


def _read_row(row_id: str) -> tracker.Row:
    """Read a single tracker row by ID."""
    for row in tracker.read_all(TRACKER_PATH):
        if row.id == row_id:
            return row
    print(json.dumps({"error": f"Row not found: {row_id}"}))
    sys.exit(1)


def _read_draft(row: tracker.Row) -> str:
    """Read the draft body from the file at body_path."""
    if not row.body_path:
        return ""
    draft_path = _PROJECT_ROOT / row.body_path
    if not draft_path.exists():
        return ""
    text = draft_path.read_text(encoding="utf-8")
    # Strip YAML frontmatter if present
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text.strip()


def _call_llm(prompt: str) -> str:
    """Call the Anthropic API and return the response text."""
    client = _get_client()
    response = client.messages.create(
        model=_get_model(),
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def _parse_scores(text: str) -> dict | None:
    """Parse JSON scores from LLM response."""
    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM response as JSON: %s", text[:200])
        return None


def rescore(row_id: str) -> dict:
    """Score a single draft against the quality rubric.

    Returns the score dict and updates tracker notes.
    """
    row = _read_row(row_id)
    if row.status not in ("drafted", "queued"):
        result = {"error": f"Row status is '{row.status}' — expected 'drafted' or 'queued'"}
        print(json.dumps(result))
        sys.exit(1)

    draft_body = _read_draft(row)
    if not draft_body:
        result = {"error": f"No draft body for row {row_id}"}
        print(json.dumps(result))
        sys.exit(1)

    prompt = _SCORING_PROMPT.format(
        company=row.company,
        role_title=row.role_title,
        role_tier=row.role_tier,
        person_name=row.person_name,
        hook=row.hook,
        draft_body=draft_body,
    )

    text = _call_llm(prompt)
    parsed = _parse_scores(text)
    if not parsed:
        result = {"error": "Failed to parse LLM scoring response"}
        print(json.dumps(result))
        sys.exit(1)

    s = parsed.get("specificity", 0)
    v = parsed.get("voice", 0)
    a = parsed.get("ask", 0)
    l = parsed.get("length", 0)
    r = parsed.get("risk", 0)
    avg = round((s + v + a + l + r) / 5, 1)
    approved = avg >= 4.0 and r >= 3

    # Build notes string
    score_str = f"quality: {s}/{v}/{a}/{l}/{r} avg={avg}"
    approval_str = f"manager_approved={'true' if approved else 'false'}"

    feedback = parsed.get("feedback", {})
    feedback_parts = []
    if not approved and feedback:
        for dim, fb in feedback.items():
            if fb:
                feedback_parts.append(f"{dim}: {fb}")

    existing_notes = row.notes or ""
    # Remove old quality/manager_approved lines
    existing_notes = re.sub(r"quality:\s*\d/\d/\d/\d/\d\s+avg=\d+(?:\.\d+)?[,;]?\s*", "", existing_notes)
    existing_notes = re.sub(r"manager_approved=\w+[,;]?\s*", "", existing_notes)
    existing_notes = re.sub(r"feedback:.*$", "", existing_notes, flags=re.MULTILINE)
    existing_notes = existing_notes.strip().rstrip(";").strip()

    new_notes_parts = [score_str, approval_str]
    if feedback_parts:
        new_notes_parts.append("feedback: " + "; ".join(feedback_parts))

    new_notes = ", ".join(new_notes_parts)
    if existing_notes:
        new_notes = f"{existing_notes}; {new_notes}"

    tracker.update_notes(row_id, new_notes, TRACKER_PATH)

    result = {
        "row_id": row_id,
        "scores": {"specificity": s, "voice": v, "ask": a, "length": l, "risk": r},
        "avg": avg,
        "approved": approved,
        "feedback": feedback,
    }
    return result


def explain(row_id: str) -> dict:
    """Return per-dimension explanation for an existing score.

    Does NOT write to tracker — read-only.
    """
    row = _read_row(row_id)

    # Parse existing score from notes
    match = _SCORE_PATTERN.search(row.notes or "")
    if not match:
        result = {"error": "No existing score — run --rescore first"}
        print(json.dumps(result))
        sys.exit(1)

    s, v, a, l, r = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4)), int(match.group(5))
    avg = float(match.group(6))
    approved = avg >= 4.0 and r >= 3

    draft_body = _read_draft(row)
    if not draft_body:
        result = {"error": f"No draft body for row {row_id}"}
        print(json.dumps(result))
        sys.exit(1)

    prompt = _EXPLAIN_PROMPT.format(
        company=row.company,
        role_title=row.role_title,
        person_name=row.person_name,
        specificity=s, voice=v, ask=a, length=l, risk=r, avg=avg,
        draft_body=draft_body,
    )

    text = _call_llm(prompt)
    explanations = _parse_scores(text)
    if not explanations:
        result = {"error": "Failed to parse LLM explanation response"}
        print(json.dumps(result))
        sys.exit(1)

    result = {
        "row_id": row_id,
        "scores": {"specificity": s, "voice": v, "ask": a, "length": l, "risk": r},
        "avg": avg,
        "approved": approved,
        "explanations": explanations,
    }
    return result


def batch_review() -> list[dict]:
    """Score all drafted rows. Returns list of score dicts."""
    drafted = tracker.read_by_status("drafted", TRACKER_PATH)
    if not drafted:
        print(json.dumps({"message": "No drafted rows to review"}))
        return []

    results = []
    for row in drafted[:15]:  # Cap at 15 per batch
        logger.info("Scoring %s (%s)", row.id, row.company)
        try:
            result = rescore(row.id)
            results.append(result)
        except SystemExit:
            # rescore calls sys.exit on error — catch and continue
            results.append({"row_id": row.id, "error": "scoring failed"})
        except Exception as exc:
            logger.error("Error scoring %s: %s", row.id, exc)
            results.append({"row_id": row.id, "error": str(exc)})

    return results


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Score outreach drafts against the quality rubric.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--rescore", metavar="ROW_ID",
        help="Score a single draft and update tracker notes.",
    )
    group.add_argument(
        "--explain", metavar="ROW_ID",
        help="Explain an existing score with per-dimension feedback (read-only).",
    )
    group.add_argument(
        "--batch-review", action="store_true",
        help="Score all drafted rows (up to 15).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.rescore:
        result = rescore(args.rescore)
        print(json.dumps(result, indent=2))
    elif args.explain:
        result = explain(args.explain)
        print(json.dumps(result, indent=2))
    elif args.batch_review:
        results = batch_review()
        print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
