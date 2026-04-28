"""Unit tests for core/scorer.py.

Stubs — real implementations come in build step 3. All tests skip
cleanly rather than passing vacuously (GUARDRAILS §1.6).
"""

import pytest


def test_tier_classification():
    """T1/T2/T3 keyword matching against role titles."""
    pytest.skip("Not yet implemented — build step 3")


def test_hard_skip_detection():
    """Deal-breaker regex matching against title + JD snippet."""
    pytest.skip("Not yet implemented — build step 3")


def test_score_calculation():
    """Rubric math: title, stage, sector, location, experience weights."""
    pytest.skip("Not yet implemented — build step 3")


def test_threshold_check():
    """Per-tier threshold enforcement (T1 >= 0.5, T2 >= 0.6, T3 >= 0.75)."""
    pytest.skip("Not yet implemented — build step 3")


def test_location_outside_india_skipped():
    """Jobs outside India without remote-from-India should hard-skip."""
    pytest.skip("Not yet implemented — build step 3")


def test_experience_parsing():
    """Experience range extraction from strings like '2-4 years', '3+'."""
    pytest.skip("Not yet implemented — build step 3")
