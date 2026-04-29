"""LLM-powered relevance agent — semantic second-pass on job fit.

After the keyword scorer (core/scorer.py) passes a job, this agent calls
Claude via the Anthropic API to make a semantic apply/skip/queue decision,
catching false positives the regex-based scorer misses.

Requires ANTHROPIC_API_KEY in .env.  Budget-capped at
MAX_RELEVANCE_API_CALLS_PER_DAY (default 400).  Results cached in
data/relevance_cache.json with a 30-day TTL.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.logger import normalize_url
from core.scorer import Job

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_DEFAULT_BUDGET = 400
_DEFAULT_MODEL = "claude-sonnet-4-6"
_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "relevance_agent.md"
_CACHE_PATH = Path("data/relevance_cache.json")
_CALLS_DIR = Path("data")


@dataclass
class RelevanceVerdict:
    """Result of the relevance agent's evaluation."""

    decision: str          # "apply" | "queue" | "skip"
    confidence: float      # 0.0–1.0
    reason: str
    red_flags: list[str] = field(default_factory=list)


class RelevanceAgent:
    """Semantic job-fit evaluator backed by the Anthropic API.

    Usage::

        agent = RelevanceAgent()
        verdict = agent.evaluate(job, "naukri")
        if verdict is None:
            # budget exceeded or agent unavailable — use keyword scorer
        elif verdict.decision == "skip":
            ...

    Args:
        cache_path: Override for the cache file location (tests).
        calls_dir: Override for the daily-counter directory (tests).
    """

    def __init__(
        self,
        cache_path: Path | None = None,
        calls_dir: Path | None = None,
    ) -> None:
        self._model: str = os.getenv("RELEVANCE_MODEL", _DEFAULT_MODEL)
        self._budget: int = int(os.getenv("MAX_RELEVANCE_API_CALLS_PER_DAY", str(_DEFAULT_BUDGET)))
        self._cache_path: Path = cache_path or _CACHE_PATH
        self._calls_dir: Path = calls_dir or _CALLS_DIR

        self._client = None  # lazy-init
        self._client_failed: bool = False  # True after first init failure
        self._prompt_template: str = ""
        self._cache: dict[str, dict] = {}
        self._cache_loaded: bool = False

    # ── Public API ───────────────────────────────────────────────────

    def evaluate(self, job: Job, platform: str) -> RelevanceVerdict | None:
        """Evaluate a job's relevance using the LLM.

        Returns:
            A RelevanceVerdict, or None if the agent is unavailable
            (missing API key) or the daily budget is exhausted.
        """
        # Lazy-init the client
        if self._client is None and not self._client_failed:
            self._init_client()
        if self._client_failed:
            return None

        # Load cache on first call
        if not self._cache_loaded:
            self._load_cache()

        # Cache check
        key = self._cache_key(platform, job.posted_date)
        cached = self._cache.get(key)
        if cached is not None:
            logger.debug("Relevance cache hit for %s/%s", platform, job.title)
            return RelevanceVerdict(
                decision=cached["decision"],
                confidence=cached["confidence"],
                reason=cached["reason"],
                red_flags=cached.get("red_flags", []),
            )

        # Budget check
        calls_today = self._read_call_count()
        if calls_today >= self._budget:
            logger.warning(
                "Relevance agent budget exhausted (%d/%d) — falling back to keyword scorer",
                calls_today, self._budget,
            )
            return None

        # Build messages and call the API
        user_msg = self._build_user_message(job)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=256,
                system=self._prompt_template,
                messages=[{"role": "user", "content": user_msg}],
            )
            text = response.content[0].text
        except Exception as exc:
            logger.error("Relevance agent API error: %s", exc)
            return RelevanceVerdict(
                decision="queue",
                confidence=0.0,
                reason=f"agent error: {exc}",
            )

        # Increment call counter
        self._write_call_count(calls_today + 1)

        # Parse response
        verdict = self._parse_response(text)

        # Cache the result
        self._cache[key] = {
            "decision": verdict.decision,
            "confidence": verdict.confidence,
            "reason": verdict.reason,
            "red_flags": verdict.red_flags,
            "cached_at": time.time(),
        }
        self._save_cache()

        logger.info(
            "Relevance agent: %s [%.2f] %s — %s",
            verdict.decision, verdict.confidence, job.title, verdict.reason,
        )
        return verdict

    # ── Internals ────────────────────────────────────────────────────

    def _init_client(self) -> None:
        """Lazy-initialise the Anthropic client and load the prompt."""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error(
                "ANTHROPIC_API_KEY not set — relevance agent disabled for this run"
            )
            self._client_failed = True
            return

        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
        except Exception as exc:
            logger.error("Failed to initialise Anthropic client: %s", exc)
            self._client_failed = True
            return

        # Load prompt template
        try:
            self._prompt_template = _PROMPT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error("Prompt template not found at %s", _PROMPT_PATH)
            self._client_failed = True
            return

    def _cache_key(self, platform: str, url: str) -> str:
        """SHA-256 hash of (platform, normalised URL)."""
        normalised = normalize_url(url)
        raw = f"{platform}:{normalised}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _load_cache(self) -> None:
        """Load the cache from disk, pruning entries older than 30 days."""
        self._cache_loaded = True
        if not self._cache_path.exists():
            self._cache = {}
            return
        try:
            raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load relevance cache: %s", exc)
            self._cache = {}
            return

        now = time.time()
        self._cache = {
            k: v for k, v in raw.items()
            if now - v.get("cached_at", 0) < _CACHE_TTL_SECONDS
        }
        pruned = len(raw) - len(self._cache)
        if pruned:
            logger.info("Pruned %d expired entries from relevance cache", pruned)

    def _save_cache(self) -> None:
        """Persist the cache to disk."""
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._cache_path.write_text(
                json.dumps(self._cache, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Could not save relevance cache: %s", exc)

    def _build_user_message(self, job: Job) -> str:
        """Format the job data into the user message for the API call."""
        parts = [
            f"Title: {job.title}",
            f"Company: {job.company}",
            f"Location: {job.location}",
            f"Experience required: {job.experience_required}",
            f"Description:\n{job.jd_text}",
        ]
        return "\n".join(parts)

    def _parse_response(self, text: str) -> RelevanceVerdict:
        """Parse JSON from model output.

        On malformed JSON, returns a queue verdict with zero confidence
        so the job goes to human review rather than being auto-skipped.
        """
        # Strip markdown fences if the model wraps its output
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (fences)
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Relevance agent returned malformed JSON: %s", text[:200])
            return RelevanceVerdict(
                decision="queue",
                confidence=0.0,
                reason="agent returned malformed JSON",
            )

        decision = str(data.get("decision", "queue")).lower()
        if decision not in ("apply", "queue", "skip"):
            decision = "queue"

        confidence = data.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        reason = str(data.get("reason", ""))
        red_flags = data.get("red_flags", [])
        if not isinstance(red_flags, list):
            red_flags = []
        red_flags = [str(f) for f in red_flags]

        return RelevanceVerdict(
            decision=decision,
            confidence=confidence,
            reason=reason,
            red_flags=red_flags,
        )

    def _calls_count_path(self) -> Path:
        """Path to the daily call counter file."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._calls_dir / f"relevance_calls_{today}.count"

    def _read_call_count(self) -> int:
        """Read today's API call count from the counter file."""
        path = self._calls_count_path()
        if not path.exists():
            return 0
        try:
            return int(path.read_text().strip())
        except (ValueError, OSError):
            return 0

    def _write_call_count(self, count: int) -> None:
        """Write the updated call count to the counter file."""
        path = self._calls_count_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(str(count), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not write call count: %s", exc)
