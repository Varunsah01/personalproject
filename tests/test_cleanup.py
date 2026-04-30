"""Tests for outreach/lib/cleanup.py.

Covers: stale-eligible statuses get closed, exempt statuses untouched,
dry-run mode, notes preservation, and fresh rows ignored.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from outreach.lib.tracker import (
    Row,
    init_tracker,
    read_all,
    update_status,
    update_notes,
    upsert,
)
from outreach.lib.cleanup import close_stale


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_row(**overrides) -> Row:
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


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


def _seed_at_status(path, status: str, days_old: int, **extra) -> str:
    """Seed a row at the given status with last_updated set to days_old ago.

    Walks through the state machine to reach the target status, then
    backdates last_updated.  Returns the row id.
    """
    chain = [
        "research_done",
        "people_found",
        "contact_found",
        "drafted",
        "queued",
        "sent",
    ]

    # linkedin_queue branches from people_found
    if status == "linkedin_queue":
        row = _make_row(status="research_done", **extra)
        upsert(row, path)
        row_id = read_all(path)[-1].id
        update_status(row_id, "people_found", path)
        update_status(row_id, "linkedin_queue", path)
    elif status == "follow_up_queued":
        row = _make_row(status="research_done", **extra)
        upsert(row, path)
        row_id = read_all(path)[-1].id
        for s in chain[1:]:  # walk to sent
            update_status(row_id, s, path)
        update_status(row_id, "follow_up_drafted", path)
        update_status(row_id, "follow_up_queued", path)
    else:
        row = _make_row(status="research_done", **extra)
        upsert(row, path)
        row_id = read_all(path)[-1].id
        idx = chain.index("research_done")
        target_idx = chain.index(status)
        for step in chain[idx + 1 : target_idx + 1]:
            update_status(row_id, step, path)

    # Backdate last_updated by rewriting the raw CSV
    import csv
    from outreach.lib.tracker import COLUMNS, _read_all_raw, _write_all

    raw = _read_all_raw(path)
    for r in raw:
        if r["id"] == row_id:
            r["last_updated"] = _days_ago(days_old)
    _write_all(raw, path)

    return row_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCloseStale:
    def test_closes_stale_eligible_rows(self, tmp_path):
        p = tmp_path / "tracker.csv"
        init_tracker(p)

        # 1. research_done, 45d → should close
        id1 = _seed_at_status(p, "research_done", 45, company="Co1", person_name="P1")
        # 2. drafted, 35d → should close
        id2 = _seed_at_status(p, "drafted", 35, company="Co2", person_name="P2")
        # 3. queued, 60d → EXEMPT
        id3 = _seed_at_status(p, "queued", 60, company="Co3", person_name="P3")
        # 4. people_found, 10d → not stale yet
        id4 = _seed_at_status(p, "people_found", 10, company="Co4", person_name="P4")
        # 5. linkedin_queue, 31d → should close
        id5 = _seed_at_status(p, "linkedin_queue", 31, company="Co5", person_name="P5")

        n = close_stale(path=p)
        assert n == 3

        rows = {r.id: r for r in read_all(p)}
        assert rows[id1].status == "closed"
        assert rows[id2].status == "closed"
        assert rows[id3].status == "queued"
        assert rows[id4].status == "people_found"
        assert rows[id5].status == "closed"

    def test_dry_run_does_not_modify(self, tmp_path):
        p = tmp_path / "tracker.csv"
        init_tracker(p)

        id1 = _seed_at_status(p, "research_done", 45, company="Co1", person_name="P1")
        id2 = _seed_at_status(p, "drafted", 35, company="Co2", person_name="P2")

        n = close_stale(dry_run=True, path=p)
        assert n == 2

        rows = {r.id: r for r in read_all(p)}
        assert rows[id1].status == "research_done"
        assert rows[id2].status == "drafted"

    def test_notes_appended(self, tmp_path):
        p = tmp_path / "tracker.csv"
        init_tracker(p)

        id1 = _seed_at_status(p, "drafted", 40, company="Co1", person_name="P1")
        update_notes(id1, "quality: S/V/A/L/R avg=4.2", p)

        # Backdate again after update_notes stamped last_updated
        from outreach.lib.tracker import _read_all_raw, _write_all
        raw = _read_all_raw(p)
        for r in raw:
            if r["id"] == id1:
                r["last_updated"] = _days_ago(40)
        _write_all(raw, p)

        close_stale(path=p)
        row = [r for r in read_all(p) if r.id == id1][0]
        assert "quality: S/V/A/L/R avg=4.2" in row.notes
        assert "auto-closed: stale > 30d at drafted" in row.notes

    def test_exempt_queued_and_follow_up_queued(self, tmp_path):
        p = tmp_path / "tracker.csv"
        init_tracker(p)

        id1 = _seed_at_status(p, "queued", 60, company="Co1", person_name="P1")
        id2 = _seed_at_status(p, "follow_up_queued", 60, company="Co2", person_name="P2")

        n = close_stale(path=p)
        assert n == 0

        rows = {r.id: r for r in read_all(p)}
        assert rows[id1].status == "queued"
        assert rows[id2].status == "follow_up_queued"

    def test_no_stale_rows_returns_zero(self, tmp_path):
        p = tmp_path / "tracker.csv"
        init_tracker(p)

        _seed_at_status(p, "research_done", 5, company="Co1", person_name="P1")
        _seed_at_status(p, "people_found", 15, company="Co2", person_name="P2")

        n = close_stale(path=p)
        assert n == 0
