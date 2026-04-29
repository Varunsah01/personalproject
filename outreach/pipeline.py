"""Outreach pipeline orchestrator — runs subagents in sequence.

CLI::

    python outreach/pipeline.py --stage all
    python outreach/pipeline.py --stage research --dry-run
    python outreach/pipeline.py --stage people

Invokes Claude Code subagents via subprocess, checks error rates between
stages, and honours the outreach/STOP file.  See guidelines.md §3.8 for
scheduling cadence.
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_STOP_PATH = Path("outreach/STOP")
_LOG_DIR = Path("data/logs")

# Stage name → agent name (matches .claude/agents/{name}.md)
STAGE_AGENTS: dict[str, str] = {
    "research": "role_researcher",
    "people": "people_finder",
    "channel": "channel_finder",
    "write": "message_writer",
}

# Execution order for --stage all
STAGE_ORDER = ["research", "people", "channel", "write"]

# If more than this fraction of rows error in a stage, halt the pipeline
_ERROR_RATE_THRESHOLD = 0.30


@dataclass
class StageResult:
    """Result of running a single pipeline stage."""

    stage: str
    agent: str
    rows_processed: int
    rows_errored: int
    output: str
    exit_code: int

    @property
    def error_rate(self) -> float:
        """Fraction of rows that errored (0.0 if none processed)."""
        if self.rows_processed == 0:
            return 0.0
        return self.rows_errored / self.rows_processed

    @property
    def halted(self) -> bool:
        """True if error rate exceeds threshold."""
        return self.error_rate > _ERROR_RATE_THRESHOLD


def _check_stop(stop_path: Path = _STOP_PATH) -> bool:
    """Return True if the STOP file exists."""
    return stop_path.exists()


def _parse_error_rate(output: str) -> tuple[int, int]:
    """Parse agent output to estimate (errored, total) row counts.

    Agents output summary tables with status columns.  We count lines
    containing "error" or "failed" (case-insensitive) as errored rows,
    and all table-like lines (starting with |) as total rows processed.

    This is best-effort — agent output is natural language, not structured.
    If parsing fails, returns (0, 0) so the pipeline doesn't halt on
    unparseable output.

    Args:
        output: Raw stdout from the agent subprocess.

    Returns:
        (errored_count, total_count) tuple.
    """
    # Look for markdown table rows (lines starting with |)
    table_rows = []
    for line in output.splitlines():
        stripped = line.strip()
        # Skip header separators (|---|---|)
        if stripped.startswith("|") and not re.match(r"^\|[\s\-|]+\|$", stripped):
            table_rows.append(stripped)

    if not table_rows:
        return (0, 0)

    # Skip the header row (first table row is usually column names)
    data_rows = table_rows[1:] if len(table_rows) > 1 else table_rows
    total = len(data_rows)

    errored = sum(
        1
        for row in data_rows
        if re.search(r"\b(error|failed|skipped)\b", row, re.IGNORECASE)
    )

    return (errored, total)


def _build_agent_prompt(stage: str, dry_run: bool) -> str:
    """Build the prompt string passed to the agent.

    Args:
        stage: Pipeline stage name.
        dry_run: If True, instruct the agent to simulate without writing.

    Returns:
        Prompt string for the agent.
    """
    prompt = (
        f"Run the {STAGE_AGENTS[stage]} agent. "
        f"Read GUARDRAILS.md and outreach/CLAUDE.md first, then process rows."
    )
    if dry_run:
        prompt += (
            " DRY RUN: do NOT write to tracker.csv or create any files. "
            "Log what you would do and output the summary table."
        )
    return prompt


def _run_stage(
    stage: str,
    *,
    dry_run: bool = False,
    log_file: Path | None = None,
) -> StageResult:
    """Run a single pipeline stage by invoking the Claude Code agent.

    Args:
        stage: Stage name (research, people, channel, write).
        dry_run: If True, append dry-run instruction to agent prompt.
        log_file: If provided, append agent output to this file.

    Returns:
        StageResult with parsed row counts and raw output.
    """
    agent_name = STAGE_AGENTS[stage]
    prompt = _build_agent_prompt(stage, dry_run)

    logger.info("Starting stage: %s (agent: %s)%s",
                stage, agent_name, " [DRY RUN]" if dry_run else "")

    try:
        result = subprocess.run(
            ["claude", "--agent", agent_name, "--print", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max per stage
        )
        output = result.stdout + result.stderr
        exit_code = result.returncode
    except FileNotFoundError:
        output = "ERROR: 'claude' command not found on PATH"
        exit_code = 127
        logger.error(output)
    except subprocess.TimeoutExpired:
        output = f"ERROR: stage '{stage}' timed out after 600 seconds"
        exit_code = 124
        logger.error(output)

    # Log output to file
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            timestamp = datetime.now(timezone.utc).isoformat()
            f.write(f"\n{'='*60}\n")
            f.write(f"Stage: {stage} | Agent: {agent_name} | {timestamp}\n")
            if dry_run:
                f.write("Mode: DRY RUN\n")
            f.write(f"Exit code: {exit_code}\n")
            f.write(f"{'='*60}\n")
            f.write(output)
            f.write("\n")

    errored, total = _parse_error_rate(output)

    stage_result = StageResult(
        stage=stage,
        agent=agent_name,
        rows_processed=total,
        rows_errored=errored,
        output=output,
        exit_code=exit_code,
    )

    logger.info(
        "Stage %s complete: %d processed, %d errored (%.0f%%), exit=%d",
        stage, total, errored,
        stage_result.error_rate * 100,
        exit_code,
    )

    return stage_result


def run_pipeline(
    stages: list[str],
    *,
    dry_run: bool = False,
    stop_path: Path = _STOP_PATH,
) -> list[StageResult]:
    """Run one or more pipeline stages in sequence.

    Checks the STOP file before starting and between each stage.
    Halts if any stage exceeds the error rate threshold.

    Args:
        stages: List of stage names to run, in order.
        dry_run: If True, agents simulate without writing.
        stop_path: Path to the STOP file.

    Returns:
        List of StageResult objects for each stage that ran.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = _LOG_DIR / f"outreach-{today}.log"

    results: list[StageResult] = []

    # Pre-flight STOP check
    if _check_stop(stop_path):
        logger.warning("STOP file present at %s — pipeline will not run", stop_path)
        return results

    for stage in stages:
        # Inter-stage STOP check
        if _check_stop(stop_path):
            logger.warning("STOP file appeared between stages — halting at '%s'", stage)
            break

        result = _run_stage(stage, dry_run=dry_run, log_file=log_file)
        results.append(result)

        # Error rate gate
        if result.halted:
            logger.error(
                "Stage '%s' error rate %.0f%% exceeds %.0f%% threshold — halting pipeline",
                stage,
                result.error_rate * 100,
                _ERROR_RATE_THRESHOLD * 100,
            )
            break

        # Non-zero exit code from the agent subprocess
        if result.exit_code != 0:
            logger.error(
                "Stage '%s' exited with code %d — halting pipeline",
                stage, result.exit_code,
            )
            break

    # Print summary
    prefix = "[DRY RUN] " if dry_run else ""
    logger.info("%sPipeline complete. Stages run: %d/%d", prefix, len(results), len(stages))
    for r in results:
        status = "HALTED" if r.halted else ("ERROR" if r.exit_code != 0 else "OK")
        logger.info(
            "  %s: %d processed, %d errored, exit=%d [%s]",
            r.stage, r.rows_processed, r.rows_errored, r.exit_code, status,
        )

    return results


def main() -> None:
    """CLI entry point for the pipeline orchestrator."""
    parser = argparse.ArgumentParser(
        description="Outreach pipeline — run subagents in sequence"
    )
    parser.add_argument(
        "--stage",
        choices=list(STAGE_AGENTS.keys()) + ["all"],
        required=True,
        help="Which stage to run (or 'all' for the full pipeline)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Agents simulate without writing to tracker or creating files",
    )
    parser.add_argument(
        "--stop-path",
        type=Path,
        default=_STOP_PATH,
        help="Path to the STOP file (default: outreach/STOP)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.stage == "all":
        stages = STAGE_ORDER
    else:
        stages = [args.stage]

    results = run_pipeline(stages, dry_run=args.dry_run, stop_path=args.stop_path)

    # Exit non-zero if any stage failed
    if any(r.halted or r.exit_code != 0 for r in results):
        sys.exit(1)
    if len(results) < len(stages):
        sys.exit(1)  # Pipeline was halted early (STOP file)


if __name__ == "__main__":
    main()
