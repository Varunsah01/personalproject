"""Unit tests for NaukriPlatform URL builder and slug helper."""

from __future__ import annotations

import pytest

from platforms.naukri import NaukriPlatform


class TestSlugify:
    """NaukriPlatform._slugify() slug normalization."""

    @pytest.mark.parametrize("text, expected", [
        ("growth manager", "growth-manager"),
        ("Delhi NCR", "delhi-ncr"),
        ("Delhi/NCR", "delhi-ncr"),
        ("revenue operations", "revenue-operations"),
        ("C++", "c-plus-plus"),
        ("  extra   spaces  ", "extra-spaces"),
        ("", ""),
        ("Bangalore", "bangalore"),
    ])
    def test_slugify(self, text: str, expected: str) -> None:
        assert NaukriPlatform._slugify(text) == expected


class TestBuildSearchUrl:
    """NaukriPlatform._build_search_url() path-segment URLs."""

    def test_growth_manager_delhi(self) -> None:
        url = NaukriPlatform._build_search_url("growth manager", "Delhi NCR", 1, 7)
        assert url == "https://www.naukri.com/growth-manager-jobs-in-delhi-ncr?experience=1&jobAge=7"

    def test_revenue_operations_delhi(self) -> None:
        url = NaukriPlatform._build_search_url("revenue operations", "Delhi NCR", 1, 7)
        assert url == "https://www.naukri.com/revenue-operations-jobs-in-delhi-ncr?experience=1&jobAge=7"

    def test_page_2_suffix(self) -> None:
        url = NaukriPlatform._build_search_url("growth manager", "Delhi NCR", 1, 7, page=2)
        assert url == "https://www.naukri.com/growth-manager-jobs-in-delhi-ncr-2?experience=1&jobAge=7"

    def test_page_5_suffix(self) -> None:
        url = NaukriPlatform._build_search_url("growth manager", "Delhi NCR", 1, 7, page=5)
        assert url == "https://www.naukri.com/growth-manager-jobs-in-delhi-ncr-5?experience=1&jobAge=7"

    def test_cpp_keyword(self) -> None:
        url = NaukriPlatform._build_search_url("C++", "Bangalore", 1, 7)
        assert url == "https://www.naukri.com/c-plus-plus-jobs-in-bangalore?experience=1&jobAge=7"

    def test_empty_location_omits_in_segment(self) -> None:
        url = NaukriPlatform._build_search_url("growth manager", "", 1, 7)
        assert url == "https://www.naukri.com/growth-manager-jobs?experience=1&jobAge=7"

    def test_different_experience_and_age(self) -> None:
        url = NaukriPlatform._build_search_url("product manager", "Mumbai", 2, 14)
        assert url == "https://www.naukri.com/product-manager-jobs-in-mumbai?experience=2&jobAge=14"


class TestNoLegacyParams:
    """Regression: old query-param format must not appear in generated URLs."""

    @pytest.mark.parametrize("keyword, location", [
        ("growth manager", "Delhi NCR"),
        ("revenue operations", "Bangalore"),
        ("C++", "Mumbai"),
    ])
    def test_no_legacy_params(self, keyword: str, location: str) -> None:
        url = NaukriPlatform._build_search_url(keyword, location, 1, 7)
        assert "nignbelow_salary" not in url
        assert "&k=" not in url
        assert "&l=" not in url
        assert "&pageNo=" not in url
