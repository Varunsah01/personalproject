"""Playwright browser setup with persistent profiles and anti-detection.

Each platform gets its own persistent browser profile at
data/browser_profiles/<platform>/. Profiles are reused across runs to
avoid looking suspicious (guidelines.md §3.3).
"""

from __future__ import annotations

from playwright.async_api import BrowserContext, Playwright


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
        playwright: Optional Playwright instance. If None, creates a new one.

    Returns:
        A BrowserContext with persistent state and anti-detection settings.
    """
    raise NotImplementedError


async def human_type(page: object, selector: str, text: str, delay: int = 80) -> None:
    """Type text into a field with human-like keystroke delays.

    Args:
        page: Playwright Page object.
        selector: CSS selector for the input field.
        text: Text to type.
        delay: Milliseconds between keystrokes.
    """
    raise NotImplementedError
