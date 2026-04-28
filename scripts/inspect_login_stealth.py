#!/usr/bin/env python3
"""Login page inspection using the same anti-detection context as the real bot.

Uses launch_persistent_context with the same args as core/browser.py so that
Cloudflare and similar bot detectors see a real-looking browser session.
Inspects the login form DOM and takes screenshots.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_WEBDRIVER_MASK = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
)


async def make_context(pw, name: str):
    profile_dir = Path(f"data/browser_profiles/{name}_inspect")
    profile_dir.mkdir(parents=True, exist_ok=True)
    ctx = await pw.chromium.launch_persistent_context(
        str(profile_dir),
        headless=False,
        slow_mo=80,
        user_agent=_USER_AGENT,
        viewport={"width": 1280, "height": 900},
        locale="en-US",
        timezone_id="Asia/Kolkata",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
        ignore_default_args=["--enable-automation"],
    )
    await ctx.add_init_script(_WEBDRIVER_MASK)
    return ctx


async def dump_inputs(page) -> list[dict]:
    return await page.evaluate("""() => {
        return [...document.querySelectorAll('input, button, a')].map(el => ({
            tag: el.tagName.toLowerCase(),
            type: el.getAttribute('type') || '',
            name: el.getAttribute('name') || '',
            id: el.getAttribute('id') || '',
            placeholder: el.getAttribute('placeholder') || '',
            class: (el.className || '').slice(0, 120),
            text: el.textContent.trim().slice(0, 60),
            outerHTML: el.outerHTML.slice(0, 300),
        })).filter(el => el.type !== 'hidden');
    }""")


async def inspect_wellfound(pw) -> dict:
    print("\n" + "=" * 60)
    print("WELLFOUND — stealth inspection")
    print("=" * 60)
    ctx = await make_context(pw, "wellfound")
    page = await ctx.new_page()
    result: dict = {}

    # Navigate to login
    try:
        await page.goto("https://wellfound.com/login", wait_until="networkidle", timeout=30_000)
    except PlaywrightTimeout:
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(5)

    await asyncio.sleep(3)
    await page.screenshot(path="data/wellfound_stealth1.png")
    print(f"  URL: {page.url}")
    print(f"  Title: {await page.title()}")

    elements = await dump_inputs(page)
    print(f"\n  All interactive elements ({len(elements)}):")
    for el in elements:
        if el['text'] or el['placeholder'] or el['type'] in ('text', 'email', 'password', 'submit'):
            print(f"    <{el['tag']} type={el['type']!r} name={el['name']!r} id={el['id']!r} "
                  f"ph={el['placeholder']!r}> [{el['text'][:40]}]")
            if el['type'] in ('text', 'email', 'password', 'submit') or el['tag'] == 'button':
                print(f"      HTML: {el['outerHTML'][:250]}")

    # Find and click email sign-in options
    for sel in [
        "text=Sign in with email",
        "text=Continue with email",
        "text=Use email",
        "[data-test*='email']",
        "button:has-text('email')",
        "a:has-text('email')",
        "a[href*='email']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                text = await el.inner_text()
                print(f"\n  Clicking: {sel!r} ({text!r})")
                await el.click()
                await asyncio.sleep(3)
                await page.screenshot(path="data/wellfound_stealth2.png")
                print(f"  After click URL: {page.url}")
                # Re-dump
                elements2 = await dump_inputs(page)
                print(f"\n  After click — inputs ({len(elements2)}):")
                for e2 in elements2:
                    if e2['type'] in ('text', 'email', 'password', 'submit') or (e2['tag'] == 'button' and e2['text']):
                        print(f"    {e2['outerHTML'][:250]}")
                break
        except Exception as exc:
            pass

    # Full form dump
    forms = await page.evaluate("""() => {
        return [...document.querySelectorAll('form')].map(f => ({
            id: f.id,
            action: f.action,
            html: f.outerHTML.slice(0, 2000),
        }));
    }""")
    print(f"\n  Forms found: {len(forms)}")
    for i, f in enumerate(forms):
        print(f"\n  Form {i+1}:\n{f['html'][:1500]}")

    # Body text to understand page state
    body_text = await page.evaluate("document.body.innerText.slice(0, 500)")
    print(f"\n  Page text preview: {body_text!r}")

    result["url"] = page.url
    result["forms"] = forms
    result["inputs"] = elements

    await ctx.close()
    return result


async def inspect_cutshort(pw) -> dict:
    print("\n" + "=" * 60)
    print("CUTSHORT — stealth inspection")
    print("=" * 60)
    ctx = await make_context(pw, "cutshort")
    page = await ctx.new_page()
    result: dict = {}

    # Navigate to home (since /login redirects)
    try:
        await page.goto("https://cutshort.io/login", wait_until="networkidle", timeout=30_000)
    except PlaywrightTimeout:
        await page.wait_for_load_state("domcontentloaded")
        await asyncio.sleep(5)

    await asyncio.sleep(3)
    await page.screenshot(path="data/cutshort_stealth1.png")
    print(f"  URL: {page.url}")
    print(f"  Title: {await page.title()}")

    # Look for "Candidate login" button and click it
    for sel in [
        "text=Candidate login",
        "text=Log in",
        "text=Sign in",
        "a[href*='/login']",
        "a[href*='candidate']",
    ]:
        try:
            el = await page.query_selector(sel)
            if el:
                text = await el.inner_text()
                print(f"\n  Clicking: {sel!r} ({text!r})")
                await el.click()
                await asyncio.sleep(3)
                await page.screenshot(path="data/cutshort_stealth2.png")
                print(f"  After click URL: {page.url}")
                break
        except Exception:
            pass

    # Dump all inputs
    elements = await dump_inputs(page)
    print(f"\n  Interactive elements ({len(elements)}):")
    for el in elements:
        if el['type'] in ('text', 'email', 'password', 'submit') or (el['tag'] in ('button', 'a') and el['text']):
            print(f"    <{el['tag']} type={el['type']!r} name={el['name']!r} id={el['id']!r} "
                  f"ph={el['placeholder']!r}> [{el['text'][:60]}]")
            print(f"      HTML: {el['outerHTML'][:250]}")

    # Full form dump
    forms = await page.evaluate("""() => {
        return [...document.querySelectorAll('form')].map(f => ({
            id: f.id,
            action: f.action,
            html: f.outerHTML.slice(0, 2000),
        }));
    }""")
    print(f"\n  Forms found: {len(forms)}")
    for i, f in enumerate(forms):
        print(f"\n  Form {i+1}:\n{f['html'][:1500]}")

    # Try to navigate directly to a login form path
    for url in [
        "https://cutshort.io/login?type=candidate",
        "https://cutshort.io/candidate/login",
        "https://cutshort.io/auth/login",
    ]:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15_000)
            await asyncio.sleep(3)
            forms2 = await page.evaluate("document.querySelectorAll('form').length")
            inputs2 = await page.evaluate("document.querySelectorAll('input[type=email],input[type=password]').length")
            if forms2 > 0 or inputs2 > 0:
                print(f"\n  Found form at: {url} ({forms2} forms, {inputs2} email/pass inputs)")
                await page.screenshot(path=f"data/cutshort_tryurl_{url.split('/')[-1]}.png")
                full = await page.evaluate("""() => {
                    return [...document.querySelectorAll('form')].map(f => f.outerHTML.slice(0, 1500)).join('\\n---\\n');
                }""")
                print(full[:1500])
                break
        except Exception as exc:
            print(f"  {url}: {exc}")

    body_text = await page.evaluate("document.body.innerText.slice(0, 500)")
    print(f"\n  Page text preview: {body_text!r}")

    result["url"] = page.url
    result["forms"] = forms
    result["inputs"] = elements

    await ctx.close()
    return result


async def main():
    Path("data").mkdir(exist_ok=True)
    all_results = {}

    async with async_playwright() as pw:
        all_results["wellfound"] = await inspect_wellfound(pw)
        all_results["cutshort"] = await inspect_cutshort(pw)

    Path("data/login_stealth_inspection.json").write_text(json.dumps(all_results, indent=2))
    print("\nSaved to data/login_stealth_inspection.json")


if __name__ == "__main__":
    asyncio.run(main())
