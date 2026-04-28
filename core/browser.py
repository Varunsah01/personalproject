"""Playwright browser setup with persistent profiles and anti-detection.

Each platform gets its own persistent browser profile at
data/browser_profiles/<platform>/. Profiles are reused across runs to
avoid looking suspicious (guidelines.md §3.3).
"""

from __future__ import annotations

import asyncio
import random
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright


# Fixed Chrome on macOS user agent — never randomised per session
# (guidelines.md §3.3). Pinned to a real, recent Chrome build string.
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Masks the automation marker injected by Chromium's DevTools Protocol.
# --disable-blink-features=AutomationControlled covers the Blink-level flag;
# this init script handles JS frameworks that check the property directly.
_WEBDRIVER_MASK = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
)


async def create_browser_context(
    platform: str,
    headless: bool = True,
    playwright: Playwright | None = None,
) -> BrowserContext:
    """Create a persistent Playwright browser context for a platform.

    Uses persistent storage at data/browser_profiles/<platform>/ so cookies,
    localStorage, and session data survive between runs.

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

    context = await playwright.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        user_agent=_USER_AGENT,
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        timezone_id="Asia/Kolkata",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        ignore_default_args=["--enable-automation"],
    )

    await context.add_init_script(_WEBDRIVER_MASK)

    return context


async def human_type(page: object, selector: str, text: str, delay: int = 80) -> None:
    """Type text into a field with human-like keystroke delays.

    Clicks to focus, triple-clicks to select and delete any existing value,
    then types character by character with random jitter around `delay` ms.
    Uses keyboard.type() rather than page.fill() per guidelines.md §3.3
    to emit real key events.

    Args:
        page: Playwright Page object.
        selector: CSS selector for the input field.
        text: Text to type.
        delay: Base milliseconds between keystrokes (±20ms random jitter).
    """
    await page.click(selector)
    await page.click(selector, click_count=3)
    await page.keyboard.press("Backspace")
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep((delay + random.randint(-20, 20)) / 1000)
