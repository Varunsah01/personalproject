"""SelectorStore — lazy YAML-backed CSS selector registry.

Each platform has a YAML file at platforms/selectors/<platform>.yaml.
SelectorStore loads it once and exposes selectors as attributes:

    sel = SelectorStore("naukri")
    sel.login_email  # → "input[placeholder*='Email']"

When a selector breaks mid-session, call ``sel.reload()`` to re-read the
YAML from disk without restarting the bot (guidelines.md §4.4).
"""

from __future__ import annotations

from pathlib import Path

import yaml


class SelectorStore:
    """CSS selector registry backed by a per-platform YAML file.

    Selectors are accessed as attributes (snake_case keys from the YAML).
    Raises ``AttributeError`` with a clear message if a key is missing,
    so the caller surfaces the exact name to add to the YAML.

    Args:
        platform: Platform name (matches the YAML filename, e.g. "naukri").
    """

    def __init__(self, platform: str) -> None:
        # Use object.__setattr__ to bypass __getattr__ during construction.
        object.__setattr__(self, "_platform", platform)
        object.__setattr__(
            self,
            "_path",
            Path("platforms/selectors") / f"{platform}.yaml",
        )
        object.__setattr__(self, "_data", {})
        self.reload()

    def reload(self) -> None:
        """Re-read the YAML file from disk.

        Call this after manually editing the YAML to hot-patch a broken
        selector without restarting the bot.
        """
        path: Path = object.__getattribute__(self, "_path")
        raw = yaml.safe_load(path.read_text()) or {}
        object.__setattr__(self, "_data", raw)

    def __getattr__(self, name: str) -> str:
        # Only reached for names not in __dict__ (i.e. not _platform/_path/_data).
        try:
            data: dict = object.__getattribute__(self, "_data")
            return data[name]
        except KeyError:
            platform: str = object.__getattribute__(self, "_platform")
            raise AttributeError(
                f"No selector '{name}' in platforms/selectors/{platform}.yaml"
            )
