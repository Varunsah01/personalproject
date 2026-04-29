"""Tests for outreach/lib/tracker.py.

Covers: idempotent upsert, state machine enforcement, case-insensitive
dedupe, and UTC-date-boundary send counting.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from outreach.lib.tracker import (
    Row,
    StateMachineError,
    count_sent_today,
    count_sent_today_by_inbox,
    dedupe_check,
    read_all,
    update_status,
    upsert,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides: str) -> Row:
    """Create a Row with sensible defaults, overridable per test."""
    defaults = {
        "company": "Acme Corp",
        "person_name": "Jane Doe",
        "role_url": "https://example.com/job/123",
        "role_title": "Growth Manager",
        "role_tier": "T1",
        "status": "research_done",
    }
    defaults.update(overrides)
    return Row(**defaults)


def _seed_row_through_status(path, target_status: str) -> str:
    """Insert a row and walk it through the state machine to *target_status*.

    Returns the row id.
    """
    chain = [
        "research_done",
        "people_found",
        "contact_found",
        "drafted",
        "queued",
        "sent",
        "replied",
        "closed",
    ]
    row = _make_row(status="research_done")
    upsert(row, path)
    rows = read_all(path)
    row_id = rows[0].id

    idx = chain.index("research_done")
    target_idx = chain.index(target_status)
    for step in chain[idx + 1 : target_idx + 1]:
        update_status(row_id, step, path)

    return row_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUpsertIdempotent:
    """upsert with the same (company, person_name) should update, not duplicate."""

    def test_second_upsert_updates_in_place(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        row1 = _make_row(person_title="CTO")
        upsert(row1, csv_path)

        row2 = _make_row(person_title="CEO")
        upsert(row2, csv_path)

        rows = read_all(csv_path)
        assert len(rows) == 1
        assert rows[0].person_title == "CEO"

    def test_different_person_creates_second_row(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        upsert(_make_row(person_name="Alice"), csv_path)
        upsert(_make_row(person_name="Bob"), csv_path)

        rows = read_all(csv_path)
        assert len(rows) == 2


class TestStateMachine:
    """update_status must reject illegal transitions and allow legal ones."""

    def test_rejects_research_done_to_sent(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        row = _make_row(status="research_done")
        upsert(row, csv_path)
        row_id = read_all(csv_path)[0].id

        with pytest.raises(StateMachineError):
            update_status(row_id, "sent", csv_path)

    def test_rejects_drafted_to_sent(self, tmp_path):
        """drafted -> sent is never legal; must pass through queued."""
        csv_path = tmp_path / "tracker.csv"
        row_id = _seed_row_through_status(csv_path, "drafted")

        with pytest.raises(StateMachineError):
            update_status(row_id, "sent", csv_path)

    def test_allows_full_legal_chain(self, tmp_path):
        """Walk through the entire legal chain without error."""
        csv_path = tmp_path / "tracker.csv"
        row_id = _seed_row_through_status(csv_path, "closed")

        row = read_all(csv_path)[0]
        assert row.status == "closed"

    def test_closed_is_reachable_from_any_state(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        row = _make_row(status="research_done")
        upsert(row, csv_path)
        row_id = read_all(csv_path)[0].id

        update_status(row_id, "closed", csv_path)
        assert read_all(csv_path)[0].status == "closed"

    def test_raises_key_error_for_missing_row(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        upsert(_make_row(), csv_path)

        with pytest.raises(KeyError):
            update_status("nonexistent-id", "people_found", csv_path)


class TestDedupeCheckCaseInsensitive:
    """dedupe_check should match regardless of case."""

    def test_matches_different_case(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        upsert(_make_row(company="Razorpay", person_name="Priya Sharma"), csv_path)

        result = dedupe_check("razorpay", "priya sharma", csv_path)
        assert result is not None
        assert result.company == "Razorpay"

    def test_returns_none_on_no_match(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        upsert(_make_row(company="Razorpay", person_name="Priya Sharma"), csv_path)

        result = dedupe_check("Razorpay", "Someone Else", csv_path)
        assert result is None


class TestCountSentToday:
    """count_sent_today must honour UTC date boundary."""

    def test_counts_only_today(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"

        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        today_iso = now.isoformat()
        yesterday_iso = yesterday.isoformat()

        # Row sent today
        row_today = _make_row(person_name="Today Person")
        upsert(row_today, csv_path)
        row_today_id = read_all(csv_path)[0].id
        _seed_to_queued(row_today_id, csv_path)
        _force_sent(row_today_id, csv_path, sent_at=today_iso, inbox="a@gmail.com")

        # Row sent yesterday
        row_yesterday = _make_row(
            company="Other Corp", person_name="Yesterday Person"
        )
        upsert(row_yesterday, csv_path)
        rows = read_all(csv_path)
        row_yesterday_id = [r for r in rows if r.person_name == "Yesterday Person"][0].id
        _seed_to_queued(row_yesterday_id, csv_path)
        _force_sent(
            row_yesterday_id, csv_path, sent_at=yesterday_iso, inbox="a@gmail.com"
        )

        assert count_sent_today(csv_path) == 1

    def test_counts_by_inbox(self, tmp_path):
        csv_path = tmp_path / "tracker.csv"
        now_iso = datetime.now(timezone.utc).isoformat()

        # Two rows, different inboxes
        r1 = _make_row(person_name="Person A")
        upsert(r1, csv_path)
        id_a = read_all(csv_path)[0].id
        _seed_to_queued(id_a, csv_path)
        _force_sent(id_a, csv_path, sent_at=now_iso, inbox="inbox1@gmail.com")

        r2 = _make_row(person_name="Person B")
        upsert(r2, csv_path)
        id_b = [r for r in read_all(csv_path) if r.person_name == "Person B"][0].id
        _seed_to_queued(id_b, csv_path)
        _force_sent(id_b, csv_path, sent_at=now_iso, inbox="inbox2@gmail.com")

        assert count_sent_today_by_inbox("inbox1@gmail.com", csv_path) == 1
        assert count_sent_today_by_inbox("inbox2@gmail.com", csv_path) == 1
        assert count_sent_today(csv_path) == 2


# ---------------------------------------------------------------------------
# Test helpers that manipulate status directly for count tests
# ---------------------------------------------------------------------------

def _seed_to_queued(row_id: str, path) -> None:
    """Walk a research_done row to queued status."""
    for status in ("people_found", "contact_found", "drafted", "queued"):
        update_status(row_id, status, path)


def _force_sent(row_id: str, path, *, sent_at: str, inbox: str) -> None:
    """Transition to sent and patch sent_at_utc/assigned_inbox directly.

    We bypass mark_sent() so we can control the timestamp for testing.
    """
    import csv as _csv
    from pathlib import Path as _Path

    from outreach.lib.tracker import COLUMNS, _read_all_raw

    update_status(row_id, "sent", _Path(path))
    raw = _read_all_raw(_Path(path))
    for row in raw:
        if row.get("id") == row_id:
            row["sent_at_utc"] = sent_at
            row["assigned_inbox"] = inbox
            break
    _Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(_Path(path), "w", newline="") as f:
        writer = _csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(raw)
