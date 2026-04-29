"""Tests for outreach/lib/digest.py.

Covers: build_subject formatting, build_body section content (funnel snapshot,
sent today with UTC boundary, awaiting review sorted oldest-first, replies,
errors), and empty-tracker behaviour.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from outreach.lib.digest import build_body, build_subject
from outreach.lib.tracker import (
    Row,
    init_tracker,
    read_all,
    update_status,
    upsert,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides: str) -> Row:
    """Create a Row with sensible defaults."""
    defaults = {
        "company": "Acme Corp",
        "person_name": "Jane Doe",
        "role_url": "https://example.com/job/123",
        "role_title": "Growth Manager",
        "role_tier": "T1",
        "person_title": "CTO",
        "email": "jane@acme.com",
        "person_country": "India",
        "status": "research_done",
    }
    defaults.update(overrides)
    return Row(**defaults)


def _walk_to(row_id: str, target: str, csv_path: Path) -> None:
    """Walk a research_done row to target status."""
    chain = [
        "research_done", "people_found", "contact_found",
        "drafted", "queued", "sent", "replied", "closed",
    ]
    start = chain.index("research_done")
    end = chain.index(target)
    for s in chain[start + 1 : end + 1]:
        update_status(row_id, s, csv_path)


def _set_field(csv_path: Path, row_id: str, field: str, value: str) -> None:
    """Directly set a field on a tracker row."""
    from outreach.lib.tracker import _read_all_raw, _write_all
    raw = _read_all_raw(csv_path)
    for r in raw:
        if r.get("id") == row_id:
            r[field] = value
            break
    _write_all(raw, csv_path)


def _seed_sent(csv_path: Path, *, company: str, person: str, role: str = "Growth Manager",
               inbox: str = "out@gmail.com", sent_at: str) -> str:
    """Insert a row and walk to sent, patching sent_at_utc. Returns row_id."""
    row = _make_row(company=company, person_name=person, role_title=role)
    upsert(row, csv_path)
    row_id = [r for r in read_all(csv_path) if r.company == company and r.person_name == person][-1].id
    _walk_to(row_id, "sent", csv_path)
    _set_field(csv_path, row_id, "sent_at_utc", sent_at)
    _set_field(csv_path, row_id, "assigned_inbox", inbox)
    return row_id


def _seed_drafted(csv_path: Path, *, company: str, person: str,
                  last_updated: str | None = None) -> str:
    """Insert a row and walk to drafted. Returns row_id."""
    row = _make_row(company=company, person_name=person)
    upsert(row, csv_path)
    row_id = [r for r in read_all(csv_path) if r.company == company and r.person_name == person][-1].id
    _walk_to(row_id, "drafted", csv_path)
    if last_updated:
        _set_field(csv_path, row_id, "last_updated", last_updated)
    return row_id


# ---------------------------------------------------------------------------
# TestBuildSubject
# ---------------------------------------------------------------------------

class TestBuildSubject:
    """build_subject produces the expected one-liner."""

    def test_with_counts(self):
        subject = build_subject("2026-04-29", {"sent": 7, "queued": 5, "drafted": 12})
        assert "2026-04-29" in subject
        assert "7 sent" in subject
        assert "5 queued" in subject
        assert "12 drafted" in subject

    def test_zeros(self):
        subject = build_subject("2026-04-29", {})
        assert "0 sent" in subject
        assert "0 queued" in subject
        assert "0 drafted" in subject


# ---------------------------------------------------------------------------
# TestFunnelSnapshot
# ---------------------------------------------------------------------------

class TestFunnelSnapshot:
    """Section 1 shows funnel counts in pipeline order."""

    def test_counts_appear(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        upsert(_make_row(company="A", person_name="P1", status="research_done"), csv_path)
        upsert(_make_row(company="B", person_name="P2", status="research_done"), csv_path)
        row_id = [r for r in read_all(csv_path) if r.company == "B"][0].id
        _walk_to(row_id, "drafted", csv_path)

        body = build_body("2026-04-29", csv_path)

        assert "FUNNEL SNAPSHOT" in body
        assert "research_done" in body
        assert "drafted" in body


# ---------------------------------------------------------------------------
# TestSentToday
# ---------------------------------------------------------------------------

class TestSentToday:
    """Section 2 lists rows sent today and excludes yesterday."""

    def test_today_rows_included(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        today = "2026-04-29"
        _seed_sent(csv_path, company="TodayCo", person="Alice",
                   sent_at=f"{today}T10:30:00+00:00", inbox="a@gmail.com")

        body = build_body(today, csv_path)

        assert "TodayCo" in body
        assert "Alice" in body
        assert "10:30" in body
        assert "a@gmail.com" in body

    def test_yesterday_rows_excluded(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        today = "2026-04-29"
        yesterday = "2026-04-28"

        _seed_sent(csv_path, company="YesterdayCo", person="Bob",
                   sent_at=f"{yesterday}T15:00:00+00:00")
        _seed_sent(csv_path, company="TodayCo", person="Alice",
                   sent_at=f"{today}T10:00:00+00:00")

        body = build_body(today, csv_path)

        assert "TodayCo" in body
        assert "YesterdayCo" not in body


# ---------------------------------------------------------------------------
# TestAwaitingReview
# ---------------------------------------------------------------------------

class TestAwaitingReview:
    """Section 3 shows drafted rows sorted oldest-first."""

    def test_sorted_oldest_first_and_shows_review_command(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        # Seed with explicit last_updated to control sort order
        _seed_drafted(csv_path, company="Newer", person="P1",
                      last_updated="2026-04-29T12:00:00+00:00")
        _seed_drafted(csv_path, company="Older", person="P2",
                      last_updated="2026-04-28T08:00:00+00:00")

        body = build_body("2026-04-29", csv_path)

        assert "AWAITING REVIEW" in body
        # Older should appear before Newer
        older_pos = body.index("Older")
        newer_pos = body.index("Newer")
        assert older_pos < newer_pos
        assert "python outreach/review.py" in body


# ---------------------------------------------------------------------------
# TestReplies
# ---------------------------------------------------------------------------

class TestReplies:
    """Section 4 shows replied rows with today's last_updated."""

    def test_reply_today_shown(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        today = "2026-04-29"

        row = _make_row(company="ReplyCo", person_name="Carol", role_title="PM")
        upsert(row, csv_path)
        row_id = [r for r in read_all(csv_path) if r.company == "ReplyCo"][0].id
        _walk_to(row_id, "replied", csv_path)
        _set_field(csv_path, row_id, "last_updated", f"{today}T14:00:00+00:00")

        body = build_body(today, csv_path)

        assert "REPLIES TODAY" in body
        assert "ReplyCo" in body
        assert "Carol" in body


# ---------------------------------------------------------------------------
# TestErrors
# ---------------------------------------------------------------------------

class TestErrors:
    """Section 5 shows rows with error keywords in notes."""

    def test_error_rows_shown(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"

        row = _make_row(company="BrokenCo", person_name="Dave")
        upsert(row, csv_path)
        row_id = [r for r in read_all(csv_path) if r.company == "BrokenCo"][0].id
        _set_field(csv_path, row_id, "notes", "permanent_failure: HTTP 400 bounce")

        body = build_body("2026-04-29", csv_path)

        assert "ERRORS / WARNINGS" in body
        assert "BrokenCo" in body
        assert "permanent_failure" in body


# ---------------------------------------------------------------------------
# TestEmptySections
# ---------------------------------------------------------------------------

class TestEmptySections:
    """Empty tracker produces appropriate "no data" messages."""

    def test_all_sections_show_empty_state(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        init_tracker(csv_path)

        body = build_body("2026-04-29", csv_path)

        assert "No messages sent today." in body
        assert "No drafts awaiting review." in body
        assert "No replies today." in body
        assert "No errors or warnings." in body
