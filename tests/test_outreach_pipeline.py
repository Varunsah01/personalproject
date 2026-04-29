"""Tests for outreach/pipeline.py.

Exercises the orchestration shape — stage sequencing, STOP-file guard,
error-rate gate, timeout/missing-binary handling, and log-file writes —
with subprocess.run mocked out so no real subagent is invoked.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from outreach.lib.tracker import Row, read_all, update_status, upsert
from outreach.pipeline import STAGE_ORDER, StageResult, run_pipeline


# ---------------------------------------------------------------------------
# Canned agent outputs
# ---------------------------------------------------------------------------

# Two rows, neither errored → 0% error rate → passes gate
_GOOD_OUTPUT = """\
| company | status |
|---|---|
| Acme | done |
| Beta | done |
"""

# One of three rows errored → 33% > 30% threshold → halts
_HIGH_ERROR_OUTPUT = """\
| company | status |
|---|---|
| Acme | error |
| Beta | done |
| Gamma | done |
"""


def _mock_run(stdout: str = _GOOD_OUTPUT, returncode: int = 0) -> MagicMock:
    """Return a mock subprocess.CompletedProcess-like object."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = ""
    m.returncode = returncode
    return m


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_tracker(tmp_path) -> tuple[Path, list[str]]:
    """Write 3 rows at different statuses. Returns (csv_path, [id1, id2, id3])."""
    csv_path = tmp_path / "tracker.csv"

    rows = [
        Row(company="Alpha Corp", person_name="Alice",
            role_title="PM", status="research_done"),
        Row(company="Beta Inc", person_name="Bob",
            role_title="GM", status="research_done"),
        Row(company="Gamma Ltd", person_name="Carol",
            role_title="Head of Growth", status="research_done"),
    ]
    for r in rows:
        upsert(r, csv_path)

    ids = [r.id for r in read_all(csv_path)]

    # Advance second row to people_found, third to contact_found
    update_status(ids[1], "people_found", csv_path)
    update_status(ids[2], "people_found", csv_path)
    update_status(ids[2], "contact_found", csv_path)

    return csv_path, ids


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAllStagesDryRun:
    """run_pipeline(['all'], dry_run=True) returns 4 StageResults, no tracker mutations."""

    def test_returns_four_results_exit_zero(self, tmp_path, seeded_tracker):
        csv_path, ids = seeded_tracker
        stop_path = tmp_path / "STOP"

        with (
            patch("outreach.pipeline.subprocess.run", return_value=_mock_run()) as mock_sub,
            patch("outreach.pipeline._LOG_DIR", tmp_path / "logs"),
        ):
            results = run_pipeline(
                STAGE_ORDER,
                dry_run=True,
                stop_path=stop_path,
            )

        assert len(results) == 4
        assert all(r.exit_code == 0 for r in results)
        assert all(not r.halted for r in results)
        assert mock_sub.call_count == 4

    def test_dry_run_flag_in_prompt(self, tmp_path, seeded_tracker):
        csv_path, _ = seeded_tracker
        stop_path = tmp_path / "STOP"

        with (
            patch("outreach.pipeline.subprocess.run", return_value=_mock_run()) as mock_sub,
            patch("outreach.pipeline._LOG_DIR", tmp_path / "logs"),
        ):
            run_pipeline(STAGE_ORDER, dry_run=True, stop_path=stop_path)

        # Every call should carry "DRY RUN" in the prompt arg
        for call in mock_sub.call_args_list:
            args = call.args[0]  # ["claude", "--agent", ..., "-p", prompt]
            prompt = args[-1]
            assert "DRY RUN" in prompt

    def test_no_tracker_mutations(self, tmp_path, seeded_tracker):
        csv_path, ids = seeded_tracker

        # Read statuses before
        before = {r.id: r.status for r in read_all(csv_path)}

        stop_path = tmp_path / "STOP"
        with (
            patch("outreach.pipeline.subprocess.run", return_value=_mock_run()),
            patch("outreach.pipeline._LOG_DIR", tmp_path / "logs"),
        ):
            run_pipeline(STAGE_ORDER, dry_run=True, stop_path=stop_path)

        after = {r.id: r.status for r in read_all(csv_path)}
        assert before == after


class TestStopFileHalts:
    """STOP file present → returns [] without invoking subagents."""

    def test_stop_before_start(self, tmp_path):
        stop_path = tmp_path / "STOP"
        stop_path.write_text("")

        with patch("outreach.pipeline.subprocess.run") as mock_sub:
            results = run_pipeline(
                STAGE_ORDER,
                stop_path=stop_path,
            )

        assert results == []
        mock_sub.assert_not_called()


class TestHighErrorRateHalts:
    """Stage exceeding 30% error rate halts the pipeline after that stage."""

    def test_first_stage_halts_pipeline(self, tmp_path):
        stop_path = tmp_path / "STOP"

        with (
            patch(
                "outreach.pipeline.subprocess.run",
                return_value=_mock_run(stdout=_HIGH_ERROR_OUTPUT),
            ),
            patch("outreach.pipeline._LOG_DIR", tmp_path / "logs"),
        ):
            results = run_pipeline(
                STAGE_ORDER,
                stop_path=stop_path,
            )

        # Only the first stage ran before halt
        assert len(results) == 1
        assert results[0].stage == "research"
        assert results[0].halted is True


class TestTimeoutHalts:
    """Subprocess timeout → exit_code=124, pipeline halts after that stage."""

    def test_timeout_sets_124_and_halts(self, tmp_path):
        stop_path = tmp_path / "STOP"

        with (
            patch(
                "outreach.pipeline.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["claude"], 600),
            ),
            patch("outreach.pipeline._LOG_DIR", tmp_path / "logs"),
        ):
            results = run_pipeline(
                STAGE_ORDER,
                stop_path=stop_path,
            )

        assert len(results) == 1
        assert results[0].exit_code == 124
        # Non-zero exit code causes halt
        assert results[0].stage == "research"


class TestClaudeNotOnPath:
    """FileNotFoundError → exit_code=127, pipeline halts gracefully."""

    def test_missing_binary_sets_127_and_halts(self, tmp_path):
        stop_path = tmp_path / "STOP"

        with (
            patch(
                "outreach.pipeline.subprocess.run",
                side_effect=FileNotFoundError,
            ),
            patch("outreach.pipeline._LOG_DIR", tmp_path / "logs"),
        ):
            results = run_pipeline(
                STAGE_ORDER,
                stop_path=stop_path,
            )

        assert len(results) == 1
        assert results[0].exit_code == 127
        assert results[0].stage == "research"


class TestLogFileWritten:
    """run_pipeline writes a log file under _LOG_DIR/outreach-{date}.log."""

    def test_log_file_created_with_stage_info(self, tmp_path):
        stop_path = tmp_path / "STOP"
        log_dir = tmp_path / "logs"

        with (
            patch("outreach.pipeline.subprocess.run", return_value=_mock_run()),
            patch("outreach.pipeline._LOG_DIR", log_dir),
        ):
            run_pipeline(["research"], stop_path=stop_path)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = log_dir / f"outreach-{today}.log"

        assert log_file.exists(), f"Expected log file at {log_file}"
        content = log_file.read_text(encoding="utf-8")
        assert "Stage: research" in content
        assert "role_researcher" in content
