"""Playwright browser setup with persistent profiles and anti-detection.

Each platform gets its own persistent browser profile at
data/browser_profiles/<platform>/. Profiles are reused across runs to
avoid looking suspicious (guidelines.md §3.3).
"""

from __future__ import annotations

import logging
from pathlib import Path

from playwright.async_api import BrowserContext, Playwright, async_playwright

logger = logging.getLogger(__name__)

# Real Chrome on Windows UA — not randomised per session (guidelines.md §3.3)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

BROWSER_PROFILES_DIR = Path("data/browser_profiles")

# Default typing delay for human-like input (ms per keystroke)
TYPING_DELAY_MS = 80


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
        playwright: Optional Playwright instance. If None, creates a new one
                    (caller is responsible for cleanup in that case).

    Returns:
        A BrowserContext with persistent state and anti-detection settings.
    """
    profile_dir = BROWSER_PROFILES_DIR / platform
    profile_dir.mkdir(parents=True, exist_ok=True)

    if playwright is None:
        pw = await async_playwright().start()
    else:
        pw = playwright

    context = await pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        headless=headless,
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 768},
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        # Avoid common bot fingerprints
        args=[
            "--disable-blink-features=AutomationControlled",
        ],
    )
    logger.info("Browser context created for %s (headless=%s)", platform, headless)
    return context


async def human_type(page, selector: str, text: str, delay: int = TYPING_DELAY_MS) -> None:
    """Type text into a field with human-like keystroke delays.

    Uses page.type() instead of page.fill() for free-text fields — fill()
    sets the value instantly which looks robotic (guidelines.md §3.3).

    Args:
        page: Playwright Page object.
        selector: CSS selector for the input field.
        text: Text to type.
        delay: Milliseconds between keystrokes.
    """
    await page.click(selector)
    await page.type(selector, text, delay=delay)
