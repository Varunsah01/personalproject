"""Unit tests for core/browser.py.

All Playwright objects are replaced with unittest.mock.AsyncMock/MagicMock — no
real browser is launched.  asyncio_mode=auto (pytest.ini) means every async def
test function is run as a coroutine without an explicit @pytest.mark.asyncio.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from core.browser import (
    BotDetectionError,
    _DEFAULT_USER_AGENT,
    _WEBDRIVER_MASK,
    create_browser_context,
    human_type,
    is_bot_challenged,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_playwright():
    """Minimal playwright stub — chromium.launch_persistent_context returns an AsyncMock context."""
    pw = MagicMock()
    mock_ctx = AsyncMock()
    pw.chromium.launch_persistent_context = AsyncMock(return_value=mock_ctx)
    return pw


@pytest.fixture
def patched_stealth(monkeypatch):
    """Replace core.browser.Stealth with a lightweight stub so tests don't need Playwright."""
    stealth_inst = MagicMock()
    stealth_inst.apply_stealth_async = AsyncMock()
    mock_cls = MagicMock(return_value=stealth_inst)
    monkeypatch.setattr("core.browser.Stealth", mock_cls)
    return mock_cls


def _make_page() -> MagicMock:
    """Page stub whose keyboard methods are awaitable."""
    page = MagicMock()
    page.click = AsyncMock()
    page.keyboard = MagicMock()
    page.keyboard.type = AsyncMock()
    page.keyboard.press = AsyncMock()
    return page


# ── BotDetectionError ─────────────────────────────────────────────────


class TestBotDetectionError:
    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(BotDetectionError):
            raise BotDetectionError("challenge found", "naukri")

    def test_carries_platform_attribute(self) -> None:
        exc = BotDetectionError("challenge", "linkedin")
        assert exc.platform == "linkedin"

    def test_str_includes_platform_and_message(self) -> None:
        exc = BotDetectionError("captcha detected", "wellfound")
        assert "wellfound" in str(exc)
        assert "captcha detected" in str(exc)

    def test_is_exception_subclass(self) -> None:
        assert issubclass(BotDetectionError, Exception)

    def test_different_platforms_stored_correctly(self) -> None:
        for platform in ("naukri", "linkedin", "cutshort"):
            exc = BotDetectionError("test", platform)
            assert exc.platform == platform


# ── is_bot_challenged ─────────────────────────────────────────────────


class TestIsBotChallenged:
    """
    is_bot_challenged checks page.title() first; only calls page.evaluate()
    if the title does not match.  All page interactions are mocked.
    """

    async def test_returns_true_for_recaptcha_iframe(self) -> None:
        page = MagicMock()
        page.title = AsyncMock(return_value="Job Search")
        page.evaluate = AsyncMock(return_value=True)  # JS finds recaptcha src
        assert await is_bot_challenged(page) is True

    async def test_returns_true_for_hcaptcha_iframe(self) -> None:
        page = MagicMock()
        page.title = AsyncMock(return_value="Naukri Jobs")
        page.evaluate = AsyncMock(return_value=True)  # JS finds hcaptcha src
        assert await is_bot_challenged(page) is True

    async def test_returns_true_for_are_you_a_human_text(self) -> None:
        page = MagicMock()
        page.title = AsyncMock(return_value="Verification Required")
        page.evaluate = AsyncMock(return_value=True)  # JS finds "Are you a human"
        assert await is_bot_challenged(page) is True

    async def test_returns_true_for_security_check_title(self) -> None:
        page = MagicMock()
        page.title = AsyncMock(return_value="Security Check")
        page.evaluate = AsyncMock(return_value=False)
        assert await is_bot_challenged(page) is True

    async def test_returns_true_for_access_denied_title(self) -> None:
        page = MagicMock()
        page.title = AsyncMock(return_value="Access Denied")
        page.evaluate = AsyncMock(return_value=False)
        assert await is_bot_challenged(page) is True

    async def test_returns_false_on_clean_page(self) -> None:
        page = MagicMock()
        page.title = AsyncMock(return_value="LinkedIn Jobs")
        page.evaluate = AsyncMock(return_value=False)
        assert await is_bot_challenged(page) is False

    async def test_security_check_title_skips_evaluate(self) -> None:
        """Title match must return True immediately without calling evaluate()."""
        page = MagicMock()
        page.title = AsyncMock(return_value="Security Check")
        page.evaluate = AsyncMock(return_value=False)
        await is_bot_challenged(page)
        page.evaluate.assert_not_called()

    async def test_access_denied_title_skips_evaluate(self) -> None:
        page = MagicMock()
        page.title = AsyncMock(return_value="Access Denied")
        page.evaluate = AsyncMock(return_value=False)
        await is_bot_challenged(page)
        page.evaluate.assert_not_called()

    async def test_title_check_is_case_insensitive(self) -> None:
        """Lowercase 'security check' in the title still triggers True."""
        page = MagicMock()
        page.title = AsyncMock(return_value="security check — please wait")
        page.evaluate = AsyncMock(return_value=False)
        assert await is_bot_challenged(page) is True

    async def test_clean_title_delegates_to_evaluate(self) -> None:
        """When the title is benign, evaluate() must be called exactly once."""
        page = MagicMock()
        page.title = AsyncMock(return_value="Wellfound Jobs")
        page.evaluate = AsyncMock(return_value=False)
        await is_bot_challenged(page)
        page.evaluate.assert_called_once()


# ── human_type ────────────────────────────────────────────────────────


class TestHumanType:
    """
    human_type flow: click(focus) → click(triple-select) → keyboard.press(Delete)
    → for each char: keyboard.type(char) + asyncio.sleep(jitter).
    asyncio.sleep is patched so tests run instantly and delays are verifiable.
    """

    async def test_each_char_typed_individually(self) -> None:
        page = _make_page()
        text = "hello"
        with patch("core.browser.asyncio.sleep", new_callable=AsyncMock):
            await human_type(page, "#input", text)
        assert page.keyboard.type.call_count == len(text)

    async def test_typed_chars_match_input_in_order(self) -> None:
        page = _make_page()
        text = "abc"
        with patch("core.browser.asyncio.sleep", new_callable=AsyncMock):
            await human_type(page, "#field", text)
        typed = [c.args[0] for c in page.keyboard.type.call_args_list]
        assert typed == list(text)

    async def test_field_clicked_once_to_focus(self) -> None:
        page = _make_page()
        with patch("core.browser.asyncio.sleep", new_callable=AsyncMock):
            await human_type(page, "#input", "x")
        # First click: plain focus click
        assert page.click.call_args_list[0] == call("#input")

    async def test_field_triple_clicked_to_select_all(self) -> None:
        page = _make_page()
        with patch("core.browser.asyncio.sleep", new_callable=AsyncMock):
            await human_type(page, "#input", "x")
        # Second click: triple-click to select existing content
        assert page.click.call_args_list[1] == call("#input", click_count=3)

    async def test_delete_pressed_to_clear_selection(self) -> None:
        page = _make_page()
        with patch("core.browser.asyncio.sleep", new_callable=AsyncMock):
            await human_type(page, "#input", "x")
        page.keyboard.press.assert_called_once_with("Delete")

    async def test_sleep_called_once_per_char(self) -> None:
        """asyncio.sleep fires exactly once per character (the per-keystroke jitter)."""
        page = _make_page()
        sleep_calls: list[float] = []

        async def capture(secs: float) -> None:
            sleep_calls.append(secs)

        text = "hi"
        with patch("core.browser.asyncio.sleep", side_effect=capture):
            await human_type(page, "#input", text)

        assert len(sleep_calls) == len(text)

    async def test_non_space_delay_within_bounds(self) -> None:
        """With delay=80, non-space jitter = 80 ± 40 → [40, 120] ms."""
        page = _make_page()
        sleep_calls: list[float] = []

        async def capture(secs: float) -> None:
            sleep_calls.append(secs)

        with patch("core.browser.asyncio.sleep", side_effect=capture):
            await human_type(page, "#input", "ab", delay=80)

        for secs in sleep_calls:
            ms = secs * 1000
            assert 40 <= ms <= 120, f"sleep {ms:.1f}ms out of expected [40, 120]ms"

    async def test_space_delay_is_longer_than_non_space(self) -> None:
        """Space gets an extra 30–120 ms, so its sleep ≥ 70 ms (= 80 − 40 + 30)."""
        page = _make_page()
        sleep_calls: list[float] = []

        async def capture(secs: float) -> None:
            sleep_calls.append(secs)

        # "a b" → index 0='a', index 1=' ', index 2='b'
        with patch("core.browser.asyncio.sleep", side_effect=capture):
            await human_type(page, "#input", "a b", delay=80)

        assert len(sleep_calls) == 3
        space_ms = sleep_calls[1] * 1000
        # min: max(80 − 40 + 30, 10) = 70 ms    max: 80 + 40 + 120 = 240 ms
        assert 70 <= space_ms <= 240, f"space sleep {space_ms:.1f}ms outside [70, 240]ms"

    async def test_empty_string_types_nothing(self) -> None:
        page = _make_page()
        with patch("core.browser.asyncio.sleep", new_callable=AsyncMock):
            await human_type(page, "#input", "")
        page.keyboard.type.assert_not_called()


# ── create_browser_context ────────────────────────────────────────────


class TestCreateBrowserContext:
    """
    create_browser_context wraps playwright.chromium.launch_persistent_context.
    All assertions inspect what was passed to the launch call.
    patched_stealth prevents the real Stealth class from running.
    """

    async def test_raises_if_playwright_is_none(self) -> None:
        with pytest.raises(ValueError, match="playwright instance is required"):
            await create_browser_context("naukri", headless=True, playwright=None)

    async def test_returns_the_launched_context(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        ctx = await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        assert ctx is mock_playwright.chromium.launch_persistent_context.return_value

    async def test_profile_path_contains_platform_name(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        await create_browser_context("wellfound", headless=True, playwright=mock_playwright)
        profile_path = mock_playwright.chromium.launch_persistent_context.call_args.args[0]
        assert "wellfound" in profile_path

    async def test_profile_path_under_browser_profiles_dir(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        profile_path = mock_playwright.chromium.launch_persistent_context.call_args.args[0]
        assert "browser_profiles" in profile_path

    async def test_headless_flag_forwarded(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        await create_browser_context("naukri", headless=False, playwright=mock_playwright)
        kwargs = mock_playwright.chromium.launch_persistent_context.call_args.kwargs
        assert kwargs["headless"] is False

    async def test_user_agent_contains_chrome(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        kwargs = mock_playwright.chromium.launch_persistent_context.call_args.kwargs
        assert "Chrome" in kwargs["user_agent"]

    async def test_default_user_agent_matches_module_constant(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UA_STRING", raising=False)
        await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        kwargs = mock_playwright.chromium.launch_persistent_context.call_args.kwargs
        assert kwargs["user_agent"] == _DEFAULT_USER_AGENT

    async def test_ua_overridable_via_env(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UA_STRING", "CustomAgent/2.0")
        await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        kwargs = mock_playwright.chromium.launch_persistent_context.call_args.kwargs
        assert kwargs["user_agent"] == "CustomAgent/2.0"

    async def test_automation_controlled_flag_disabled(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        kwargs = mock_playwright.chromium.launch_persistent_context.call_args.kwargs
        assert "--disable-blink-features=AutomationControlled" in kwargs["args"]

    async def test_enable_automation_excluded_from_defaults(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        """--enable-automation must be silenced via ignore_default_args."""
        await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        kwargs = mock_playwright.chromium.launch_persistent_context.call_args.kwargs
        assert "--enable-automation" in kwargs["ignore_default_args"]

    async def test_webdriver_mask_init_script_added(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        ctx = await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        ctx.add_init_script.assert_called_once()
        script = ctx.add_init_script.call_args.args[0]
        assert "webdriver" in script

    async def test_webdriver_mask_script_matches_module_constant(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        ctx = await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        script = ctx.add_init_script.call_args.args[0]
        assert script == _WEBDRIVER_MASK

    async def test_stealth_applied_to_returned_context(
        self, mock_playwright: MagicMock, patched_stealth: MagicMock
    ) -> None:
        ctx = await create_browser_context("naukri", headless=True, playwright=mock_playwright)
        stealth_inst = patched_stealth.return_value
        stealth_inst.apply_stealth_async.assert_called_once_with(ctx)
