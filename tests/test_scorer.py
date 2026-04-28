"""Unit tests for core/scorer.py."""

from core.scorer import (
    Job,
    classify_tier,
    experience_match,
    location_match,
    score_job,
    sector_match,
    should_apply,
    stage_match,
    title_match,
)


# ── Helpers ────────────────────────────────────────────────────────────

def _make_job(**overrides) -> Job:
    """Build a Job with sensible defaults, overriding as needed."""
    defaults = {
        "title": "Product Manager",
        "company": "Acme",
        "location": "Bangalore",
        "experience_required": "2-4 years",
        "jd_text": "We are a B2B SaaS startup.",
        "posted_date": "2026-04-25",
    }
    defaults.update(overrides)
    return Job(**defaults)


# ── Tier classification ───────────────────────────────────────────────

class TestClassifyTier:
    def test_founding_member_is_t1(self) -> None:
        assert classify_tier("Founding Member — Growth") == "T1"

    def test_product_manager_is_t1(self) -> None:
        assert classify_tier("Product Manager") == "T1"

    def test_head_of_growth_is_t1(self) -> None:
        assert classify_tier("Head of Growth") == "T1"

    def test_strategy_and_operations_is_t1(self) -> None:
        assert classify_tier("Strategy and Operations Lead") == "T1"

    def test_bdm_is_t1(self) -> None:
        assert classify_tier("Business Development Manager — SaaS") == "T1"

    def test_partnerships_manager_is_t2(self) -> None:
        assert classify_tier("Partnerships Manager") == "T2"

    def test_vc_analyst_is_t2(self) -> None:
        assert classify_tier("VC Analyst — Early Stage Fund") == "T2"

    def test_revenue_operations_is_t2(self) -> None:
        assert classify_tier("Revenue Operations Manager") == "T2"

    def test_chief_of_staff_is_t3(self) -> None:
        assert classify_tier("Chief of Staff") == "T3"

    def test_program_manager_is_t3(self) -> None:
        assert classify_tier("Program Manager") == "T3"

    def test_marketing_manager_is_t3(self) -> None:
        assert classify_tier("Marketing Manager — B2B Content") == "T3"

    def test_random_title_is_skip(self) -> None:
        assert classify_tier("DevOps Engineer") == "skip"

    def test_case_insensitive(self) -> None:
        assert classify_tier("FOUNDING MEMBER") == "T1"


# ── Scoring: high-fit scenarios ────────────────────────────────────────

class TestHighFitScoring:
    def test_founding_member_series_a_saas_bangalore(self) -> None:
        """Founding Member at Series A SaaS in Bangalore should score >= 0.7."""
        job = _make_job(
            title="Founding Member — Growth",
            company="RocketPay",
            location="Bangalore",
            experience_required="2-4 years",
            jd_text=(
                "We are a Series A B2B SaaS company building GTM tooling. "
                "Looking for a founding member to own growth from 0 to 1."
            ),
        )
        score = score_job(job)
        print(f"\nFounding Member / Series A SaaS / Bangalore: {score:.4f}")
        assert score >= 0.7

    def test_product_manager_remote_seed(self) -> None:
        """Product Manager at seed-stage remote India should score >= 0.6."""
        job = _make_job(
            title="Product Manager",
            company="SeedAI",
            location="Remote India",
            experience_required="1-3 years",
            jd_text=(
                "Seed-stage AI startup looking for a product manager. "
                "We're building a marketplace for GenAI tools."
            ),
        )
        score = score_job(job)
        print(f"\nProduct Manager / Seed / Remote India: {score:.4f}")
        assert score >= 0.6


# ── Scoring: low-fit scenarios ─────────────────────────────────────────

class TestLowFitScoring:
    def test_mnc_fmcg_scores_low(self) -> None:
        """MNC FMCG role should score < 0.3 OR trigger hard-skip."""
        job = _make_job(
            title="Area Sales Manager",
            company="Unilever India",
            location="Mumbai",
            experience_required="5-8 years",
            jd_text=(
                "Fortune 500 FMCG conglomerate. Manage regional field sales "
                "teams across Maharashtra. 7+ years experience required."
            ),
        )
        score = score_job(job)
        _, reason = should_apply(job, score, classify_tier(job.title))
        print(f"\nMNC FMCG Area Sales Manager: score={score:.4f}, reason={reason}")
        # Either scores below 0.3 or gets hard-skipped
        assert score < 0.3 or "hard skip" in reason


# ── Hard-skip detection ────────────────────────────────────────────────

class TestHardSkip:
    def test_five_plus_years_hard_skip(self) -> None:
        """'5+ years required' triggers hard skip regardless of fit."""
        job = _make_job(
            title="Growth Manager",
            company="GreatStartup",
            location="Bangalore",
            experience_required="5+ years",
            jd_text="Looking for a growth manager with 5+ years experience in SaaS.",
        )
        score = score_job(job)
        apply, reason = should_apply(job, score, classify_tier(job.title))
        print(f"\nGrowth Manager but 5+ years: score={score:.4f}, apply={apply}, reason={reason}")
        assert not apply
        assert "5+ years" in reason

    def test_three_to_five_years_not_skipped(self) -> None:
        """'3-5 years' is within range and should NOT trigger hard skip."""
        job = _make_job(
            title="Growth Manager",
            company="GreatStartup",
            location="Bangalore",
            experience_required="3-5 years",
            jd_text="Looking for a growth manager with 3-5 years experience in SaaS.",
        )
        apply, reason = should_apply(job, score_job(job), classify_tier(job.title))
        print(f"\nGrowth Manager 3-5 years: apply={apply}, reason={reason}")
        assert apply

    def test_seven_years_minimum_hard_skip(self) -> None:
        """'minimum 7 years' triggers hard skip."""
        job = _make_job(
            title="Product Manager",
            jd_text="Requires minimum 7 years of product management experience.",
        )
        apply, reason = should_apply(job, score_job(job), classify_tier(job.title))
        print(f"\nPM minimum 7 years: apply={apply}, reason={reason}")
        assert not apply
        assert "5+ years" in reason

    def test_insurance_field_sales_hard_skip(self) -> None:
        """Insurance agent / field sales triggers hard skip."""
        job = _make_job(
            title="Insurance Sales Executive",
            company="LIC",
            location="Delhi",
            experience_required="0-2 years",
            jd_text="We need insurance agent to sell policies door to door.",
        )
        apply, reason = should_apply(job, score_job(job), classify_tier(job.title))
        print(f"\nInsurance Sales: apply={apply}, reason={reason}")
        assert not apply
        assert "field sales" in reason

    def test_real_estate_sales_hard_skip(self) -> None:
        """Real estate sales triggers hard skip."""
        job = _make_job(
            title="Sales Manager",
            jd_text="Real estate sales broker needed for premium property sales in Gurgaon.",
            location="Gurgaon",
        )
        apply, reason = should_apply(job, score_job(job), classify_tier(job.title))
        print(f"\nReal estate sales: apply={apply}, reason={reason}")
        assert not apply
        assert "field sales" in reason

    def test_bond_clause_hard_skip(self) -> None:
        """Bond/lock-in clause triggers hard skip."""
        job = _make_job(
            title="Product Manager",
            jd_text="2-year service bond required. Bond period starts from date of joining.",
        )
        apply, reason = should_apply(job, score_job(job), classify_tier(job.title))
        print(f"\nBond clause PM: apply={apply}, reason={reason}")
        assert not apply
        assert "bond" in reason

    def test_outside_india_hard_skip(self) -> None:
        """Location outside India triggers hard skip."""
        job = _make_job(
            title="Growth Manager",
            location="San Francisco, CA",
            jd_text="B2B SaaS startup looking for growth manager.",
        )
        apply, reason = should_apply(job, score_job(job), classify_tier(job.title))
        print(f"\nGrowth Manager SF: apply={apply}, reason={reason}")
        assert not apply
        assert "India" in reason

    def test_mlm_hard_skip(self) -> None:
        """MLM / 'be your own boss' triggers hard skip."""
        job = _make_job(
            title="Business Manager",
            jd_text="Be your own boss! Earn unlimited income with our MLM network.",
        )
        apply, reason = should_apply(job, score_job(job), classify_tier(job.title))
        print(f"\nMLM role: apply={apply}, reason={reason}")
        assert not apply
        assert "MLM" in reason

    def test_backend_engineer_hard_skip(self) -> None:
        """Pure backend engineer triggers hard skip."""
        job = _make_job(
            title="Backend Engineer",
            jd_text="Senior backend developer needed for microservices.",
            location="Bangalore",
        )
        apply, reason = should_apply(job, score_job(job), classify_tier(job.title))
        print(f"\nBackend Engineer: apply={apply}, reason={reason}")
        assert not apply
        assert "backend" in reason


# ── Threshold enforcement ──────────────────────────────────────────────

class TestThresholdEnforcement:
    def test_t1_below_threshold_rejected(self) -> None:
        """T1 role with score below 0.5 is rejected."""
        job = _make_job(title="Growth Manager", jd_text="No details.")
        apply, reason = should_apply(job, 0.45, "T1")
        assert not apply
        assert "below threshold" in reason

    def test_t1_at_threshold_accepted(self) -> None:
        """T1 role at exactly 0.5 is accepted."""
        job = _make_job(title="Growth Manager")
        apply, _ = should_apply(job, 0.50, "T1")
        assert apply

    def test_t2_below_threshold_rejected(self) -> None:
        """T2 role with score below 0.6 is rejected."""
        job = _make_job(title="Partnerships Manager")
        apply, reason = should_apply(job, 0.55, "T2")
        assert not apply
        assert "below threshold" in reason

    def test_t3_below_threshold_rejected(self) -> None:
        """T3 role with score below 0.75 is rejected."""
        job = _make_job(title="Program Manager")
        apply, reason = should_apply(job, 0.70, "T3")
        assert not apply
        assert "below threshold" in reason

    def test_skip_tier_rejected(self) -> None:
        """'skip' tier is always rejected."""
        job = _make_job(title="DevOps Engineer")
        apply, reason = should_apply(job, 0.99, "skip")
        assert not apply
        assert "no matching tier" in reason


# ── Component helpers ──────────────────────────────────────────────────

class TestComponentHelpers:
    def test_title_match_t1(self) -> None:
        assert title_match("Founding Member") == 1.0

    def test_title_match_t2(self) -> None:
        assert title_match("Partnerships Manager") == 0.7

    def test_title_match_t3(self) -> None:
        assert title_match("Chief of Staff") == 0.4

    def test_title_match_partial(self) -> None:
        assert title_match("VP of Growth") == 0.3

    def test_title_match_zero(self) -> None:
        assert title_match("DevOps Engineer") == 0.0

    def test_stage_match_seed(self) -> None:
        assert stage_match("We are a seed-stage company", "StartupX") == 1.0

    def test_stage_match_mnc(self) -> None:
        assert stage_match("Large MNC with global operations", "BigCorp") == 0.2

    def test_stage_match_no_signal(self) -> None:
        assert stage_match("We build software", "Acme") == 0.5

    def test_sector_match_multiple(self) -> None:
        assert sector_match("B2B SaaS fintech AI platform") == 1.0

    def test_sector_match_none(self) -> None:
        assert sector_match("We make furniture") == 0.1

    def test_location_match_preferred(self) -> None:
        assert location_match("Bangalore, India") == 1.0

    def test_location_match_non_preferred(self) -> None:
        assert location_match("London, UK") == 0.0

    def test_experience_match_in_range(self) -> None:
        assert experience_match("2-4 years") == 1.0

    def test_experience_match_wide_range(self) -> None:
        assert experience_match("1-5 years") == 1.0

    def test_experience_match_high(self) -> None:
        assert experience_match("8 years") == 0.2

    def test_experience_match_empty(self) -> None:
        assert experience_match("") == 0.5
