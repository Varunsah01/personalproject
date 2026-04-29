"""Playwright browser setup with persistent profiles and anti-detection.

Each platform gets its own persistent browser profile at
data/browser_profiles/<platform>/. Profiles are reused across runs to
avoid looking suspicious (guidelines.md §3.3).
"""

from __future__ import annotations

import asyncio
import os
import random
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright
from playwright_stealth import Stealth


# Fixed Chrome on macOS user agent — never randomised per session
# (guidelines.md §3.3). Pinned to a real, recent Chrome build string.
# Override via UA_STRING in .env if needed.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Masks the automation marker injected by Chromium's DevTools Protocol.
# Belt-and-suspenders alongside playwright-stealth — stealth patches the
# same property but this init script covers edge cases where stealth's
# hook fires after page JS has already read the flag.
_WEBDRIVER_MASK = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
)

# Viewport pool — one is picked at random per session.  All common
# real-world resolutions so they don't stand out in analytics.
_VIEWPORTS = [
    {"width": 1280, "height": 768},
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1920, "height": 1080},
]


class BotDetectionError(Exception):
    """Raised when the bot hits a detection challenge it cannot bypass.

    Platforms modules should catch this and stop the current platform run
    (guidelines.md §3.4 — auth_challenge).

    Attributes:
        platform: Name of the platform that triggered the detection.
    """

    def __init__(self, message: str, platform: str) -> None:
        self.platform = platform
        super().__init__(f"[{platform}] {message}")


async def create_browser_context(
    platform: str,
    headless: bool = True,
    playwright: Playwright | None = None,
) -> BrowserContext:
    """Create a persistent Playwright browser context for a platform.

    Uses persistent storage at data/browser_profiles/<platform>/ so cookies,
    localStorage, and session data survive between runs.  Applies
    playwright-stealth to every page (including popups) and randomises the
    viewport per session.

    Args:
        platform: Platform name (e.g. 'naukri'). Used as the profile dir name.
        headless: Whether to run headless. Configurable via .env HEADLESS.
        playwright: Playwright instance from async_playwright().start(). Required.

    Returns:
        A BrowserContext with persistent state and anti-detection settings.

    Raises:
        ValueError: If playwright is None.
    """
    if playwright is None:
        raise ValueError(
            "playwright instance is required — "
            "caller must call async_playwright().start() first"
        )

    profile_dir = Path("data/browser_profiles") / platform
    profile_dir.mkdir(parents=True, exist_ok=True)

    viewport = random.choice(_VIEWPORTS)
    user_agent = os.environ.get("UA_STRING", _DEFAULT_USER_AGENT)

    context = await playwright.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        user_agent=user_agent,
        viewport=viewport,
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        ignore_default_args=["--enable-automation"],
    )

    await context.add_init_script(_WEBDRIVER_MASK)

    # Apply playwright-stealth patches to the entire context — this covers
    # all current and future pages (including popups from target=_blank).
    _stealth = Stealth(
        navigator_languages_override=("en-IN", "en"),
    )
    await _stealth.apply_stealth_async(context)

    return context


async def human_type(
    page: Page, selector: str, text: str, delay: int = 80
) -> None:
    """Type text into a field with human-like keystroke delays.

    Clicks to focus, triple-clicks to select and delete any existing value,
    then types character by character with random jitter around ``delay`` ms.
    Spaces get an extra pause to mimic word-boundary hesitation.

    Uses keyboard.type() rather than page.fill() per guidelines.md §3.3
    to emit real key events.

    Args:
        page: Playwright Page object.
        selector: CSS selector for the input field.
        text: Text to type.
        delay: Base milliseconds between keystrokes (±40 ms random jitter).
    """
    await page.click(selector)
    await page.click(selector, click_count=3)
    await page.keyboard.press("Delete")

    for char in text:
        await page.keyboard.type(char)
        jitter = delay + random.randint(-40, 40)
        if char == " ":
            # Humans pause slightly at word boundaries
            jitter += random.randint(30, 120)
        await asyncio.sleep(max(jitter, 10) / 1000)


async def human_click(page: Page, selector: str) -> None:
    """Click an element with human-like mouse movement and timing.

    Waits for the element, moves to its centre with small random offset,
    pauses briefly (simulating reading/deciding), then clicks.

    Args:
        page: Playwright Page object.
        selector: CSS selector for the element to click.
    """
    element = await page.wait_for_selector(selector, timeout=10_000)
    box = await element.bounding_box() if element else None

    if box is None:
        # Element exists in DOM but has no layout (hidden, zero-size) —
        # fall back to a plain Playwright click which handles this.
        await page.click(selector)
        return

    x = box["x"] + box["width"] / 2 + random.randint(-5, 5)
    y = box["y"] + box["height"] / 2 + random.randint(-5, 5)

    # Brief pause — simulates the human reading the button before clicking
    await asyncio.sleep(random.randint(100, 350) / 1000)
    await page.mouse.click(x, y)


async def is_bot_challenged(page: Page) -> bool:
    """Detect common bot-challenge signals on the current page.

    Returns True if any of the following are found:
    - An iframe whose ``src`` contains "recaptcha" or "hcaptcha"
    - Visible text matching "Are you a human" or "Verify you are human"
    - Page title containing "Security Check" or "Access Denied"

    This is a fast heuristic, not exhaustive — it covers the most common
    challenges seen on Indian job boards.
    """
    title = await page.title()
    title_lower = title.lower()
    if "security check" in title_lower or "access denied" in title_lower:
        return True

    # JS check for CAPTCHA iframes and challenge text in one evaluate call
    # to avoid multiple round-trips.
    return await page.evaluate("""() => {
        const iframes = document.querySelectorAll('iframe[src]');
        for (const f of iframes) {
            const src = f.src.toLowerCase();
            if (src.includes('recaptcha') || src.includes('hcaptcha')) return true;
        }
        const text = document.body ? document.body.innerText : '';
        if (text.includes('Are you a human') || text.includes('Verify you are human'))
            return true;
        return false;
    }""")
