#!/usr/bin/env python3
"""Inspect live login page DOM for Wellfound and Cutshort.

Waits for network-idle (SPA fully rendered) then dumps form structure
and takes screenshots so we can identify the correct selectors.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


async def inspect_page(pw, url: str, name: str, wait: str = "networkidle") -> dict:
    print(f"\n{'='*60}\n{name.upper()}: {url}\n{'='*60}")
    browser = await pw.chromium.launch(headless=False, slow_mo=50)
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = await ctx.new_page()
    result: dict = {}

    try:
        await page.goto(url, wait_until=wait, timeout=30_000)
    except PlaywrightTimeout:
        print("  Timed out on networkidle — falling back to domcontentloaded + sleep")
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(5)

    # Extra wait for SPA rendering
    await asyncio.sleep(3)

    # Screenshot
    ss_path = f"data/{name}_login_inspect.png"
    await page.screenshot(path=ss_path)
    print(f"  Screenshot: {ss_path}")
    print(f"  Final URL: {page.url}")
    print(f"  Title: {await page.title()}")

    # Dump all form elements
    form_info = await page.evaluate("""() => {
        const forms = [...document.querySelectorAll('form')];
        return forms.map(f => ({
            id: f.id,
            action: f.action,
            html: f.outerHTML.slice(0, 1200),
        }));
    }""")
    print(f"\n  Forms found: {len(form_info)}")
    for i, f in enumerate(form_info):
        print(f"\n  Form {i+1} (id={f['id']!r}, action={f['action']!r}):")
        print(f"  {f['html'][:800]}")
    result["forms"] = form_info

    # Dump all inputs
    inputs = await page.evaluate("""() => {
        return [...document.querySelectorAll('input, button[type="submit"]')].map(el => ({
            tag: el.tagName.toLowerCase(),
            type: el.type,
            name: el.name,
            id: el.id,
            placeholder: el.placeholder,
            class: el.className.slice(0, 80),
            outerHTML: el.outerHTML.slice(0, 200),
        }));
    }""")
    print(f"\n  Inputs/buttons found: {len(inputs)}")
    for inp in inputs:
        print(f"    <{inp['tag']} type={inp['type']!r} name={inp['name']!r} id={inp['id']!r} placeholder={inp['placeholder']!r}>")
    result["inputs"] = inputs

    # Look for any apply/auth related buttons
    buttons = await page.evaluate("""() => {
        return [...document.querySelectorAll('button, a[href*="login"], a[href*="signin"]')].slice(0, 20).map(el => ({
            tag: el.tagName.toLowerCase(),
            text: el.textContent.trim().slice(0, 60),
            href: el.getAttribute('href') || '',
            class: el.className.slice(0, 80),
        }));
    }""")
    print(f"\n  Key buttons/links:")
    for b in buttons:
        if b['text']:
            print(f"    <{b['tag']}> {b['text']!r}  class={b['class'][:50]!r}  href={b['href']!r}")

    await ctx.close()
    await browser.close()
    return result


async def main():
    Path("data").mkdir(exist_ok=True)
    all_results = {}

    async with async_playwright() as pw:
        all_results["linkedin"]  = await inspect_page(pw, "https://www.linkedin.com/login", "linkedin")
        all_results["wellfound"] = await inspect_page(pw, "https://wellfound.com/login", "wellfound")
        all_results["cutshort"]  = await inspect_page(pw, "https://cutshort.io/login", "cutshort")

    Path("data/login_page_inspection.json").write_text(json.dumps(all_results, indent=2))
    print("\nSaved to data/login_page_inspection.json")


if __name__ == "__main__":
    asyncio.run(main())
