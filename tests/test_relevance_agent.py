"""Tests for core/relevance_agent.py — LLM relevance agent.

Mocks the Anthropic client so no real API calls are made.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.relevance_agent import RelevanceAgent, RelevanceVerdict
from core.scorer import Job


# ── Helpers ──────────────────────────────────────────────────────────

def _make_job(**overrides) -> Job:
    """Create a Job with sensible defaults."""
    defaults = {
        "title": "Growth Manager",
        "company": "Acme Startup",
        "location": "Bangalore",
        "experience_required": "2-4 years",
        "jd_text": "We are looking for a growth manager to drive user acquisition...",
        "posted_date": "https://example.com/jobs/123",
    }
    defaults.update(overrides)
    return Job(**defaults)


def _mock_api_response(text: str) -> MagicMock:
    """Build a mock Anthropic messages.create() return value."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


def _agent_with_mock(
    tmp_path: Path,
    response_text: str,
    budget: int = 400,
) -> tuple[RelevanceAgent, MagicMock]:
    """Create a RelevanceAgent with a mocked Anthropic client.

    Returns (agent, mock_client) so tests can inspect calls.
    """
    agent = RelevanceAgent(
        cache_path=tmp_path / "cache.json",
        calls_dir=tmp_path,
    )
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_api_response(response_text)
    agent._client = mock_client
    agent._client_failed = False
    agent._prompt_template = "You are a test prompt."
    agent._budget = budget
    return agent, mock_client


# ── Verdict parsing tests ────────────────────────────────────────────

class TestVerdictParsing:
    """Test that API responses are correctly parsed into RelevanceVerdict."""

    def test_apply_verdict_parsed(self, tmp_path: Path) -> None:
        text = json.dumps({
            "decision": "apply",
            "confidence": 0.92,
            "reason": "Strong growth role at early-stage SaaS startup.",
            "red_flags": [],
        })
        agent, _ = _agent_with_mock(tmp_path, text)
        verdict = agent.evaluate(_make_job(), "naukri")

        assert verdict is not None
        assert verdict.decision == "apply"
        assert verdict.confidence == 0.92
        assert verdict.reason == "Strong growth role at early-stage SaaS startup."
        assert verdict.red_flags == []

    def test_skip_verdict_parsed(self, tmp_path: Path) -> None:
        text = json.dumps({
            "decision": "skip",
            "confidence": 0.95,
            "reason": "Pure backend engineering role.",
            "red_flags": ["backend engineer", "requires Go and Kubernetes"],
        })
        agent, _ = _agent_with_mock(tmp_path, text)
        verdict = agent.evaluate(_make_job(title="Backend Engineer"), "linkedin")

        assert verdict is not None
        assert verdict.decision == "skip"
        assert verdict.confidence == 0.95
        assert len(verdict.red_flags) == 2
        assert "backend engineer" in verdict.red_flags

    def test_queue_verdict_parsed(self, tmp_path: Path) -> None:
        text = json.dumps({
            "decision": "queue",
            "confidence": 0.55,
            "reason": "JD is ambiguous — could be growth or marketing ops.",
            "red_flags": ["unclear seniority"],
        })
        agent, _ = _agent_with_mock(tmp_path, text)
        verdict = agent.evaluate(_make_job(), "wellfound")

        assert verdict is not None
        assert verdict.decision == "queue"
        assert 0.5 <= verdict.confidence <= 0.6

    def test_malformed_json_returns_queue(self, tmp_path: Path) -> None:
        agent, _ = _agent_with_mock(tmp_path, "This is not JSON at all!")
        verdict = agent.evaluate(_make_job(), "naukri")

        assert verdict is not None
        assert verdict.decision == "queue"
        assert verdict.confidence == 0.0
        assert "malformed" in verdict.reason.lower()

    def test_markdown_fenced_json_parsed(self, tmp_path: Path) -> None:
        """Model sometimes wraps JSON in markdown fences."""
        inner = json.dumps({
            "decision": "apply",
            "confidence": 0.88,
            "reason": "Good fit.",
            "red_flags": [],
        })
        text = f"```json\n{inner}\n```"
        agent, _ = _agent_with_mock(tmp_path, text)
        verdict = agent.evaluate(_make_job(), "naukri")

        assert verdict is not None
        assert verdict.decision == "apply"
        assert verdict.confidence == 0.88

    def test_confidence_clamped_to_range(self, tmp_path: Path) -> None:
        text = json.dumps({
            "decision": "apply",
            "confidence": 1.5,
            "reason": "Over-confident.",
            "red_flags": [],
        })
        agent, _ = _agent_with_mock(tmp_path, text)
        verdict = agent.evaluate(_make_job(), "naukri")

        assert verdict is not None
        assert verdict.confidence == 1.0

    def test_invalid_decision_defaults_to_queue(self, tmp_path: Path) -> None:
        text = json.dumps({
            "decision": "maybe",
            "confidence": 0.5,
            "reason": "Not sure.",
            "red_flags": [],
        })
        agent, _ = _agent_with_mock(tmp_path, text)
        verdict = agent.evaluate(_make_job(), "naukri")

        assert verdict is not None
        assert verdict.decision == "queue"


# ── Cache tests ──────────────────────────────────────────────────────

class TestCache:
    """Test cache hit/miss/TTL behaviour."""

    def test_cache_hit_skips_api(self, tmp_path: Path) -> None:
        agent, mock_client = _agent_with_mock(tmp_path, json.dumps({
            "decision": "apply", "confidence": 0.9,
            "reason": "Good.", "red_flags": [],
        }))
        job = _make_job()

        # First call — API hit
        v1 = agent.evaluate(job, "naukri")
        assert mock_client.messages.create.call_count == 1

        # Second call — cache hit, no API
        v2 = agent.evaluate(job, "naukri")
        assert mock_client.messages.create.call_count == 1
        assert v2 is not None
        assert v2.decision == "apply"

    def test_cache_miss_calls_api(self, tmp_path: Path) -> None:
        agent, mock_client = _agent_with_mock(tmp_path, json.dumps({
            "decision": "skip", "confidence": 0.95,
            "reason": "Skip.", "red_flags": [],
        }))

        agent.evaluate(_make_job(posted_date="https://example.com/job/1"), "naukri")
        agent.evaluate(_make_job(posted_date="https://example.com/job/2"), "naukri")
        assert mock_client.messages.create.call_count == 2

    def test_cache_ttl_expired(self, tmp_path: Path) -> None:
        agent, mock_client = _agent_with_mock(tmp_path, json.dumps({
            "decision": "apply", "confidence": 0.9,
            "reason": "Good.", "red_flags": [],
        }))
        job = _make_job()

        # First call populates cache
        agent.evaluate(job, "naukri")
        assert mock_client.messages.create.call_count == 1

        # Manually expire the cache entry
        for k in agent._cache:
            agent._cache[k]["cached_at"] = time.time() - (31 * 24 * 3600)

        # Reload cache from memory (simulate what _load_cache does on TTL check)
        # Force a fresh load by resetting the loaded flag
        agent._save_cache()
        agent._cache_loaded = False

        agent.evaluate(job, "naukri")
        assert mock_client.messages.create.call_count == 2

    def test_normalize_url_in_cache_key(self, tmp_path: Path) -> None:
        """URLs with different tracking params should produce the same cache key."""
        agent, mock_client = _agent_with_mock(tmp_path, json.dumps({
            "decision": "apply", "confidence": 0.9,
            "reason": "Good.", "red_flags": [],
        }))

        job1 = _make_job(posted_date="https://example.com/job/1?utm_source=google")
        job2 = _make_job(posted_date="https://example.com/job/1?utm_source=linkedin")

        agent.evaluate(job1, "naukri")
        agent.evaluate(job2, "naukri")
        # Same normalised URL → only 1 API call
        assert mock_client.messages.create.call_count == 1


# ── Budget tests ─────────────────────────────────────────────────────

class TestBudget:
    """Test daily API call budget enforcement."""

    def test_budget_exceeded_returns_none(self, tmp_path: Path) -> None:
        agent, mock_client = _agent_with_mock(tmp_path, json.dumps({
            "decision": "apply", "confidence": 0.9,
            "reason": "Good.", "red_flags": [],
        }), budget=2)

        # Use 2 different jobs to avoid cache hits
        agent.evaluate(_make_job(posted_date="https://example.com/1"), "naukri")
        agent.evaluate(_make_job(posted_date="https://example.com/2"), "naukri")

        # Third call should be budget-blocked
        result = agent.evaluate(_make_job(posted_date="https://example.com/3"), "naukri")
        assert result is None
        assert mock_client.messages.create.call_count == 2

    def test_budget_resets_daily(self, tmp_path: Path) -> None:
        agent, _ = _agent_with_mock(tmp_path, json.dumps({
            "decision": "apply", "confidence": 0.9,
            "reason": "Good.", "red_flags": [],
        }), budget=2)

        # Write a counter for a different date
        old_counter = tmp_path / "relevance_calls_2020-01-01.count"
        old_counter.write_text("999")

        # Today's counter should be 0
        assert agent._read_call_count() == 0


# ── Error handling tests ─────────────────────────────────────────────

class TestErrorHandling:
    """Test graceful degradation on API errors and missing config."""

    def test_api_error_returns_queue(self, tmp_path: Path) -> None:
        agent, mock_client = _agent_with_mock(tmp_path, "")
        mock_client.messages.create.side_effect = RuntimeError("API timeout")

        verdict = agent.evaluate(_make_job(), "naukri")
        assert verdict is not None
        assert verdict.decision == "queue"
        assert verdict.confidence == 0.0
        assert "agent error" in verdict.reason

    def test_missing_api_key_returns_none(self, tmp_path: Path) -> None:
        agent = RelevanceAgent(
            cache_path=tmp_path / "cache.json",
            calls_dir=tmp_path,
        )
        # Ensure no API key is set
        with patch.dict("os.environ", {}, clear=True):
            # Force re-init
            agent._client = None
            agent._client_failed = False
            verdict = agent.evaluate(_make_job(), "naukri")

        assert verdict is None


# ── Prompt construction tests ────────────────────────────────────────

class TestPromptConstruction:
    """Test that the user message includes all job data."""

    def test_prompt_includes_job_data(self, tmp_path: Path) -> None:
        agent, mock_client = _agent_with_mock(tmp_path, json.dumps({
            "decision": "apply", "confidence": 0.9,
            "reason": "Good.", "red_flags": [],
        }))
        job = _make_job(
            title="Head of Growth",
            company="Razorpay",
            location="Bangalore",
            experience_required="2-4 years",
            jd_text="Drive user acquisition across all channels...",
        )
        agent.evaluate(job, "naukri")

        call_args = mock_client.messages.create.call_args
        user_msg = call_args.kwargs["messages"][0]["content"]
        assert "Head of Growth" in user_msg
        assert "Razorpay" in user_msg
        assert "Bangalore" in user_msg
        assert "2-4 years" in user_msg
        assert "user acquisition" in user_msg
