"""Platform registry. Maps platform name → dotted import path of the class.

Adding a new platform: add one entry here. No edits needed in apply.py.
Platforms not yet built are listed with their expected path so apply.py
gives a meaningful ImportError rather than a KeyError.
"""

PLATFORM_REGISTRY: dict[str, str] = {
    "naukri": "platforms.naukri.NaukriPlatform",
    "linkedin": "platforms.linkedin.LinkedInPlatform",
    "wellfound": "platforms.wellfound.WellfoundPlatform",
    "cutshort": "platforms.cutshort.CutshortPlatform",
}
