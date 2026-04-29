"""Tests for outreach/review.py.

Covers: approve happy path, reject path, edit-then-approve, quit-mid-batch,
cooldown blocks approval, draft warnings, no rows found, filter by row-id.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from outreach.lib.tracker import Row, read_all, update_status, upsert
from outreach.review import review


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides: str) -> Row:
    """Create a Row with sensible defaults for review tests."""
    defaults = {
        "company": "Acme Corp",
        "person_name": "Jane Doe",
        "role_url": "https://example.com/job/123",
        "role_title": "Growth Manager",
        "role_tier": "T1",
        "person_title": "CTO",
        "person_linkedin": "https://linkedin.com/in/jane",
        "person_country": "India",
        "email": "jane@acme.com",
        "email_confidence": "high",
        "hook": "Saw your Series A post",
        "subject": "Founding operator role at Acme",
        "status": "research_done",
    }
    defaults.update(overrides)
    return Row(**defaults)


def _seed_drafted(tmp_path, csv_path, *, body_text="Hi, reaching out about your role.", **row_kw) -> str:
    """Insert a row, walk it to drafted, write a draft file. Returns row_id."""
    # Ensure unique company+person_name to avoid dedupe collisions
    row = _make_row(**row_kw)
    upsert(row, csv_path)

    rows = read_all(csv_path)
    # Find the row we just inserted by matching company+person_name
    target = [
        r for r in rows
        if r.company == row.company and r.person_name == row.person_name
    ]
    row_id = target[-1].id  # latest if duplicates

    # Walk through state machine to drafted
    for s in ("people_found", "contact_found", "drafted"):
        update_status(row_id, s, csv_path)

    # Write draft file
    drafts_dir = tmp_path / "drafts"
    drafts_dir.mkdir(exist_ok=True)
    draft_path = drafts_dir / f"{row_id}.md"
    draft_path.write_text(body_text, encoding="utf-8")

    # Update body_path in tracker via raw CSV manipulation
    _set_field(csv_path, row_id, "body_path", str(draft_path))

    return row_id


def _set_field(csv_path: Path, row_id: str, field: str, value: str) -> None:
    """Directly set a field on a tracker row (test helper)."""
    from outreach.lib.tracker import COLUMNS, _read_all_raw, _write_all
    raw = _read_all_raw(csv_path)
    for r in raw:
        if r.get("id") == row_id:
            r[field] = value
            break
    _write_all(raw, csv_path)


def _make_input_fn(responses: list[str]) -> callable:
    """Return a callable that yields successive responses, simulating input()."""
    it = iter(responses)
    def mock_input(prompt=""):
        return next(it)
    return mock_input


def _force_sent(csv_path: Path, row_id: str, *, sent_at: str, inbox: str = "out@gmail.com") -> None:
    """Walk a queued row to sent and patch sent_at_utc directly."""
    update_status(row_id, "sent", csv_path)
    from outreach.lib.tracker import _read_all_raw, _write_all
    raw = _read_all_raw(csv_path)
    for r in raw:
        if r.get("id") == row_id:
            r["sent_at_utc"] = sent_at
            r["assigned_inbox"] = inbox
            break
    _write_all(raw, csv_path)


# ---------------------------------------------------------------------------
# TestApproveHappyPath
# ---------------------------------------------------------------------------

class TestApproveHappyPath:
    """approve moves a drafted row to queued."""

    def test_approve_moves_to_queued(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        row_id = _seed_drafted(tmp_path, csv_path)

        result = review(
            row_id=row_id,
            tracker_path=csv_path,
            input_fn=_make_input_fn(["a"]),
        )

        assert result["approved"] == 1
        row = [r for r in read_all(csv_path) if r.id == row_id][0]
        assert row.status == "queued"


# ---------------------------------------------------------------------------
# TestRejectPath
# ---------------------------------------------------------------------------

class TestRejectPath:
    """reject moves to closed and saves notes."""

    def test_reject_with_reason(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        row_id = _seed_drafted(tmp_path, csv_path)

        result = review(
            row_id=row_id,
            tracker_path=csv_path,
            input_fn=_make_input_fn(["r", "Not a good fit"]),
        )

        assert result["rejected"] == 1
        row = [r for r in read_all(csv_path) if r.id == row_id][0]
        assert row.status == "closed"
        assert row.notes == "Not a good fit"


# ---------------------------------------------------------------------------
# TestEditThenApprove
# ---------------------------------------------------------------------------

class TestEditThenApprove:
    """edit opens editor, then user approves."""

    def test_edit_then_approve(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        row_id = _seed_drafted(tmp_path, csv_path)

        with patch("outreach.review._open_in_editor"):
            result = review(
                row_id=row_id,
                tracker_path=csv_path,
                input_fn=_make_input_fn(["e", "a"]),
            )

        assert result["approved"] == 1
        row = [r for r in read_all(csv_path) if r.id == row_id][0]
        assert row.status == "queued"


# ---------------------------------------------------------------------------
# TestQuitMidBatch
# ---------------------------------------------------------------------------

class TestQuitMidBatch:
    """quit after first row leaves remaining rows unchanged."""

    def test_quit_leaves_remaining_drafted(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"

        id1 = _seed_drafted(tmp_path, csv_path, company="Alpha", person_name="Person A")
        id2 = _seed_drafted(tmp_path, csv_path, company="Beta", person_name="Person B")
        id3 = _seed_drafted(tmp_path, csv_path, company="Gamma", person_name="Person C")

        result = review(
            tracker_path=csv_path,
            input_fn=_make_input_fn(["a", "q"]),
        )

        assert result["approved"] == 1

        rows = {r.id: r for r in read_all(csv_path)}
        assert rows[id1].status == "queued"
        assert rows[id2].status == "drafted"
        assert rows[id3].status == "drafted"


# ---------------------------------------------------------------------------
# TestCooldownBlocksApproval
# ---------------------------------------------------------------------------

class TestCooldownBlocksApproval:
    """cooldown blocks approval; user can then skip."""

    def test_cooldown_blocks_then_skip(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"

        # Create a previously-sent row for the same email
        sent_row = _make_row(
            company="Old Corp", person_name="Old Person",
            email="jane@acme.com", status="research_done",
        )
        upsert(sent_row, csv_path)
        sent_id = [r for r in read_all(csv_path) if r.company == "Old Corp"][0].id
        for s in ("people_found", "contact_found", "drafted", "queued"):
            update_status(sent_id, s, csv_path)
        recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        _force_sent(csv_path, sent_id, sent_at=recent)

        # Now seed a drafted row with the same email
        drafted_id = _seed_drafted(
            tmp_path, csv_path,
            company="New Corp", person_name="New Person",
            email="jane@acme.com",
        )

        # First "a" should be blocked by cooldown, then "s" to skip
        result = review(
            row_id=drafted_id,
            tracker_path=csv_path,
            input_fn=_make_input_fn(["a", "s"]),
        )

        assert result["approved"] == 0
        assert result["skipped"] == 1
        row = [r for r in read_all(csv_path) if r.id == drafted_id][0]
        assert row.status == "drafted"


# ---------------------------------------------------------------------------
# TestDraftWarnings
# ---------------------------------------------------------------------------

class TestDraftWarnings:
    """soft warnings for empty or long drafts."""

    def test_empty_draft_warning(self, tmp_path, capsys):
        csv_path = tmp_path / "tracker.csv"
        row_id = _seed_drafted(tmp_path, csv_path, body_text="")

        review(
            row_id=row_id,
            tracker_path=csv_path,
            input_fn=_make_input_fn(["s"]),
        )

        captured = capsys.readouterr()
        assert "Draft is empty" in captured.out

    def test_long_draft_warning(self, tmp_path, capsys):
        csv_path = tmp_path / "tracker.csv"
        long_body = ". ".join(f"Sentence {i}" for i in range(9)) + "."
        row_id = _seed_drafted(tmp_path, csv_path, body_text=long_body)

        review(
            row_id=row_id,
            tracker_path=csv_path,
            input_fn=_make_input_fn(["s"]),
        )

        captured = capsys.readouterr()
        assert "sentences" in captured.out
        assert "recommended" in captured.out


# ---------------------------------------------------------------------------
# TestNoRowsFound
# ---------------------------------------------------------------------------

class TestNoRowsFound:
    """returns zeros when no drafted rows exist."""

    def test_empty_tracker(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        from outreach.lib.tracker import init_tracker
        init_tracker(csv_path)

        result = review(tracker_path=csv_path, input_fn=_make_input_fn([]))

        assert result == {"reviewed": 0, "approved": 0, "skipped": 0, "rejected": 0}


# ---------------------------------------------------------------------------
# TestFilterByRowId
# ---------------------------------------------------------------------------

class TestFilterByRowId:
    """--row-id filters to a single row."""

    def test_only_target_row_reviewed(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"

        _seed_drafted(tmp_path, csv_path, company="Alpha", person_name="Person A")
        id2 = _seed_drafted(tmp_path, csv_path, company="Beta", person_name="Person B")
        _seed_drafted(tmp_path, csv_path, company="Gamma", person_name="Person C")

        result = review(
            row_id=id2,
            tracker_path=csv_path,
            input_fn=_make_input_fn(["a"]),
        )

        assert result["approved"] == 1
        assert result["reviewed"] == 1

        rows = {r.id: r for r in read_all(csv_path)}
        assert rows[id2].status == "queued"
