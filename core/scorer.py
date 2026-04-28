"""Job scoring engine.

Scores jobs against Varun's profile using the rubric from CLAUDE.md §6.
Configuration (role tiers, deal-breakers, preferred sectors) is injected
at init — this module never reads profile.md directly.
"""

from __future__ import annotations

import logging
import re

from core.types import Job

logger = logging.getLogger(__name__)

# --- Default configuration (mirrors profile.md and CLAUDE.md §3) ---
# These are the defaults; apply.py can override at init.

TIER_1_KEYWORDS: list[str] = [
    "growth manager",
    "head of growth",
    "product manager",
    "associate product manager",
    "associate pm",
    "strategy and operations",
    "strategy & operations",
    "business development manager",
    "founding team",
    "founding member",
    "early employee",
]

TIER_2_KEYWORDS: list[str] = [
    "partnerships manager",
    "gtm",
    "go-to-market",
    "go to market",
    "revenue operations",
    "vc analyst",
    "vc associate",
    "strategy consultant",
    "strategy analyst",
]

TIER_3_KEYWORDS: list[str] = [
    "marketing manager",
    "account executive",
    "chief of staff",
    "program manager",
]

# Thresholds per tier (source of truth: guidelines.md §4.2)
TIER_THRESHOLDS: dict[str, float] = {
    "T1": 0.5,
    "T2": 0.6,
    "T3": 0.75,
}

# Deal-breaker patterns matched against title + first 500 chars of JD
HARD_SKIP_PATTERNS: list[tuple[str, str]] = [
    (r"\b[5-9]\+?\s*(?:years?|yrs?)\b.*\b(?:experience|exp)\b", "5+ years required"),
    (r"\b(?:experience|exp)\b.*\b[5-9]\+?\s*(?:years?|yrs?)\b", "5+ years required"),
    (r"\bbackend\s+(?:engineer|developer)\b", "pure backend engineering"),
    (r"\bdata\s+engineer", "pure data engineering"),
    (r"\b(?:ui|ux|graphic)\s+design", "pure design role"),
    (r"\bbond\b|\block[- ]?in\b", "bond/lock-in clause"),
    (r"\binsurance\s+(?:agent|advisor|sales)\b", "field sales non-tech"),
    (r"\breal\s+estate\s+(?:agent|sales)\b", "field sales non-tech"),
    (r"\bdirect\s+sell", "field sales non-tech"),
]

# Preferred sectors for sector_match scoring
PREFERRED_SECTORS: list[str] = [
    "b2b saas",
    "saas",
    "fintech",
    "edtech",
    "sales-tech",
    "salestech",
    "gtm tooling",
    "ai",
    "genai",
    "gen ai",
    "artificial intelligence",
    "marketplace",
]

# Preferred locations for location_match scoring
PREFERRED_LOCATIONS: list[str] = [
    "delhi",
    "delhi ncr",
    "ncr",
    "gurgaon",
    "gurugram",
    "noida",
    "bangalore",
    "bengaluru",
    "mumbai",
    "remote",
    "pan india",
    "work from home",
    "wfh",
]

# Company stage keywords for stage_match scoring
STAGE_KEYWORDS: dict[str, float] = {
    "seed": 1.0,
    "pre-seed": 1.0,
    "series a": 1.0,
    "series b": 1.0,
    "series c": 1.0,
    "early stage": 1.0,
    "early-stage": 1.0,
    "startup": 0.9,
    "growth stage": 0.7,
    "growth-stage": 0.7,
    "series d": 0.5,
    "series e": 0.4,
    "mnc": 0.2,
    "fortune 500": 0.2,
    "large enterprise": 0.2,
}


class JobScorer:
    """Scores jobs against the configured role profile.

    Args:
        tier_1: Keywords for Tier 1 roles.
        tier_2: Keywords for Tier 2 roles.
        tier_3: Keywords for Tier 3 roles.
        thresholds: Per-tier apply thresholds.
        hard_skip_patterns: Regex patterns that trigger hard skip.
        preferred_sectors: Sector keywords for scoring.
        preferred_locations: Location keywords for scoring.
    """

    def __init__(
        self,
        tier_1: list[str] | None = None,
        tier_2: list[str] | None = None,
        tier_3: list[str] | None = None,
        thresholds: dict[str, float] | None = None,
        hard_skip_patterns: list[tuple[str, str]] | None = None,
        preferred_sectors: list[str] | None = None,
        preferred_locations: list[str] | None = None,
    ) -> None:
        self.tier_1 = tier_1 or TIER_1_KEYWORDS
        self.tier_2 = tier_2 or TIER_2_KEYWORDS
        self.tier_3 = tier_3 or TIER_3_KEYWORDS
        self.thresholds = thresholds or TIER_THRESHOLDS
        self.hard_skip_patterns = hard_skip_patterns or HARD_SKIP_PATTERNS
        self.preferred_sectors = preferred_sectors or PREFERRED_SECTORS
        self.preferred_locations = preferred_locations or PREFERRED_LOCATIONS

    def get_tier(self, role_title: str) -> str | None:
        """Classify a role title into T1, T2, T3, or None.

        Uses case-insensitive substring matching against tier keyword lists.
        Returns the highest-priority tier that matches (T1 > T2 > T3).
        """
        title_lower = role_title.lower()
        for keyword in self.tier_1:
            if keyword in title_lower:
                return "T1"
        for keyword in self.tier_2:
            if keyword in title_lower:
                return "T2"
        for keyword in self.tier_3:
            if keyword in title_lower:
                return "T3"
        return None

    def check_hard_skip(self, job: Job) -> str | None:
        """Check if a job matches any deal-breaker pattern.

        Matches against title + jd_snippet (first 500 chars).

        Returns:
            Reason string if the job should be hard-skipped, None otherwise.
        """
        text = f"{job.role_title} {job.jd_snippet}".lower()
        for pattern, reason in self.hard_skip_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return f"hard skip: {reason}"

        # Location check: outside India unless remote-from-India
        location_lower = job.location.lower()
        india_indicators = ["india", "delhi", "bangalore", "bengaluru", "mumbai",
                            "gurgaon", "gurugram", "noida", "hyderabad", "pune",
                            "chennai", "kolkata", "ncr", "remote", "pan india"]
        if location_lower and not any(ind in location_lower for ind in india_indicators):
            return "hard skip: outside India"

        return None

    def score_job(self, job: Job) -> float:
        """Compute fit score 0.0-1.0 using the rubric from CLAUDE.md §6.

        score = 0.4 * title_match
              + 0.25 * stage_match
              + 0.15 * sector_match
              + 0.10 * location_match
              + 0.10 * experience_match
        """
        title = self._score_title(job.role_title)
        stage = self._score_stage(job.jd_snippet, job.company_name)
        sector = self._score_sector(job.jd_snippet)
        location = self._score_location(job.location)
        experience = self._score_experience(job.experience_required)

        score = (
            0.40 * title
            + 0.25 * stage
            + 0.15 * sector
            + 0.10 * location
            + 0.10 * experience
        )
        return round(score, 4)

    def passes_threshold(self, score: float, tier: str) -> bool:
        """Check if a score meets the threshold for its tier."""
        threshold = self.thresholds.get(tier)
        if threshold is None:
            # Unknown tier — don't apply
            return False
        return score >= threshold

    # --- Private scoring helpers ---

    def _score_title(self, role_title: str) -> float:
        """How well the title matches target role keywords."""
        title_lower = role_title.lower()
        # Exact or near-exact matches on T1 keywords score highest
        for keyword in self.tier_1:
            if keyword in title_lower:
                return 1.0
        for keyword in self.tier_2:
            if keyword in title_lower:
                return 0.7
        for keyword in self.tier_3:
            if keyword in title_lower:
                return 0.4
        # Partial signals
        partial_signals = ["growth", "product", "strategy", "founding", "gtm", "operations"]
        for signal in partial_signals:
            if signal in title_lower:
                return 0.3
        return 0.0

    def _score_stage(self, jd_snippet: str, company_name: str) -> float:
        """How well the company stage matches preference (seed-to-C)."""
        text = f"{company_name} {jd_snippet}".lower()
        best = 0.0
        for keyword, value in STAGE_KEYWORDS.items():
            if keyword in text:
                best = max(best, value)
        # Default to 0.5 if no stage signal — assume mid-stage
        return best if best > 0 else 0.5

    def _score_sector(self, jd_snippet: str) -> float:
        """How well the company/role sector matches preferred sectors."""
        text = jd_snippet.lower()
        matches = sum(1 for sector in self.preferred_sectors if sector in text)
        if matches >= 3:
            return 1.0
        if matches == 2:
            return 0.8
        if matches == 1:
            return 0.5
        return 0.1

    def _score_location(self, location: str) -> float:
        """How well the job location matches preferred locations."""
        loc_lower = location.lower()
        for pref in self.preferred_locations:
            if pref in loc_lower:
                return 1.0
        return 0.0

    def _score_experience(self, experience_required: str) -> float:
        """How well the experience requirement matches 1-5 years.

        Extracts numeric ranges from strings like '2-4 years', '3+ years'.
        """
        if not experience_required:
            return 0.5  # unknown, assume neutral

        # Try to find a range like "2-4" or "3-5"
        range_match = re.search(r"(\d+)\s*[-–]\s*(\d+)", experience_required)
        if range_match:
            low, high = int(range_match.group(1)), int(range_match.group(2))
            # Varun has ~3 years of experience; 1-5 year range = 1.0
            if low <= 3 <= high:
                return 1.0
            if low <= 5 and high >= 1:
                return 0.7
            return 0.2

        # Try single number like "3+ years" or "5 years"
        single_match = re.search(r"(\d+)\+?\s*(?:years?|yrs?)", experience_required, re.IGNORECASE)
        if single_match:
            num = int(single_match.group(1))
            if num <= 3:
                return 1.0
            if num <= 5:
                return 0.7
            return 0.2

        return 0.5  # can't parse, assume neutral
