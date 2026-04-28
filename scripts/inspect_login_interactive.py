#!/usr/bin/env python3
"""Interactive login form inspector for Wellfound and Cutshort.

Navigates to the login page, clicks through any gating UI (OAuth buttons,
"Candidate login" landing page, etc.), waits for the email/password form
to appear, then dumps the form DOM structure so we can identify selectors.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout


async def dump_inputs(page) -> list[dict]:
    return await page.evaluate("""() => {
        return [...document.querySelectorAll('input, button[type="submit"], button[type="button"]')].map(el => ({
            tag: el.tagName.toLowerCase(),
            type: el.type,
            name: el.name,
            id: el.id,
            placeholder: el.placeholder,
            class: el.className.slice(0, 120),
            outerHTML: el.outerHTML.slice(0, 250),
        }));
    }""")


async def dump_forms(page) -> list[dict]:
    return await page.evaluate("""() => {
        return [...document.querySelectorAll('form')].map(f => ({
            id: f.id,
            action: f.action,
            html: f.outerHTML.slice(0, 1500),
        }));
    }""")


async def inspect_wellfound(pw) -> dict:
    print("\n" + "=" * 60)
    print("WELLFOUND — interactive login form inspection")
    print("=" * 60)

    browser = await pw.chromium.launch(headless=False, slow_mo=80)
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

    # Navigate to login page
    await page.goto("https://wellfound.com/login", wait_until="domcontentloaded")
    await asyncio.sleep(4)
    await page.screenshot(path="data/wellfound_step1.png")
    print(f"  Step 1 URL: {page.url}")
    print(f"  Title: {await page.title()}")

    # Look for any "Sign in with email" or "Continue with email" button
    email_btn_candidates = [
        "text=Sign in with email",
        "text=Continue with email",
        "text=Use email",
        "text=Email",
        "a[href*='email']",
        "button:has-text('email')",
        "[data-test*='email']",
    ]
    email_btn = None
    for cand in email_btn_candidates:
        try:
            el = await page.query_selector(cand)
            if el:
                text = await el.inner_text()
                print(f"  Found email button candidate: {cand!r} text={text!r}")
                email_btn = el
                break
        except Exception:
            pass

    # Dump all visible buttons and links
    buttons = await page.evaluate("""() => {
        return [...document.querySelectorAll('button, a')].slice(0, 30).map(el => ({
            tag: el.tagName.toLowerCase(),
            text: el.textContent.trim().slice(0, 80),
            href: el.getAttribute('href') || '',
            class: el.className.slice(0, 80),
        })).filter(b => b.text);
    }""")
    print(f"\n  Visible buttons/links ({len(buttons)}):")
    for b in buttons[:20]:
        print(f"    <{b['tag']}> {b['text']!r}  class={b['class'][:50]!r}")

    # If we found an email button, click it
    if email_btn:
        print(f"\n  Clicking email sign-in button...")
        await email_btn.click()
        await asyncio.sleep(3)
        await page.screenshot(path="data/wellfound_step2.png")
        print(f"  After click URL: {page.url}")

    # Now dump inputs
    inputs = await dump_inputs(page)
    forms = await dump_forms(page)

    print(f"\n  Forms: {len(forms)}")
    for i, f in enumerate(forms):
        print(f"\n  Form {i+1} (id={f['id']!r}):")
        print(f"  {f['html'][:1200]}")

    print(f"\n  Inputs ({len(inputs)}):")
    for inp in inputs:
        if inp['type'] not in ('hidden', 'submit'):
            print(f"    <{inp['tag']} type={inp['type']!r} name={inp['name']!r} id={inp['id']!r} "
                  f"placeholder={inp['placeholder']!r} class={inp['class'][:60]!r}>")

    result["url"] = page.url
    result["forms"] = forms
    result["inputs"] = [i for i in inputs if i['type'] not in ('hidden',)]

    # Also check the full page body for class patterns
    body_classes = await page.evaluate("""() => {
        const allClasses = new Set();
        document.querySelectorAll('[class]').forEach(el => {
            el.className.split(' ').forEach(c => { if(c.includes('login') || c.includes('auth') || c.includes('email') || c.includes('password') || c.includes('submit')) allClasses.add(c); });
        });
        return [...allClasses].slice(0, 40);
    }""")
    print(f"\n  Login-related CSS classes on page: {body_classes}")

    await ctx.close()
    await browser.close()
    return result


async def inspect_cutshort(pw) -> dict:
    print("\n" + "=" * 60)
    print("CUTSHORT — interactive login form inspection")
    print("=" * 60)

    browser = await pw.chromium.launch(headless=False, slow_mo=80)
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

    # Navigate to home page (since /login redirects there)
    await page.goto("https://cutshort.io/", wait_until="networkidle", timeout=30_000)
    await asyncio.sleep(3)
    await page.screenshot(path="data/cutshort_home.png")
    print(f"  Home URL: {page.url}")

    # Find and click "Candidate login" button
    candidate_btn = None
    for cand in ["text=Candidate login", "text=Sign in", "text=Log in", "button:has-text('login')", "a[href*='/login']"]:
        try:
            el = await page.query_selector(cand)
            if el:
                text = await el.inner_text()
                print(f"  Found candidate: {cand!r} text={text!r}")
                candidate_btn = el
                break
        except Exception:
            pass

    if candidate_btn:
        print(f"  Clicking Candidate login button...")
        await candidate_btn.click()
        await asyncio.sleep(3)
        await page.screenshot(path="data/cutshort_step2.png")
        print(f"  After click URL: {page.url}")

    # Dump current state
    inputs = await dump_inputs(page)
    forms = await dump_forms(page)

    print(f"\n  Forms: {len(forms)}")
    for i, f in enumerate(forms):
        print(f"\n  Form {i+1} (id={f['id']!r}):")
        print(f"  {f['html'][:1200]}")

    print(f"\n  Inputs ({len(inputs)}):")
    for inp in inputs:
        if inp['type'] not in ('hidden',):
            print(f"    <{inp['tag']} type={inp['type']!r} name={inp['name']!r} id={inp['id']!r} "
                  f"placeholder={inp['placeholder']!r} class={inp['class'][:80]!r}>")
            if inp.get('outerHTML'):
                print(f"      HTML: {inp['outerHTML'][:200]}")

    # Check for Google/LinkedIn OAuth buttons (many modern platforms use these)
    oauth_buttons = await page.evaluate("""() => {
        return [...document.querySelectorAll('button, a')].filter(el => {
            const t = el.textContent.toLowerCase();
            return t.includes('google') || t.includes('linkedin') || t.includes('github') ||
                   t.includes('email') || t.includes('sign in') || t.includes('log in');
        }).map(el => ({
            tag: el.tagName.toLowerCase(),
            text: el.textContent.trim().slice(0, 80),
            href: el.getAttribute('href') || '',
            class: el.className.slice(0, 80),
        }));
    }""")
    print(f"\n  OAuth/login buttons: {len(oauth_buttons)}")
    for b in oauth_buttons:
        print(f"    {b['tag']} | {b['text']!r} | {b['class'][:60]!r}")

    result["url"] = page.url
    result["forms"] = forms
    result["inputs"] = [i for i in inputs if i['type'] not in ('hidden',)]

    await ctx.close()
    await browser.close()
    return result


async def main():
    Path("data").mkdir(exist_ok=True)
    all_results = {}

    async with async_playwright() as pw:
        all_results["wellfound"] = await inspect_wellfound(pw)
        all_results["cutshort"] = await inspect_cutshort(pw)

    Path("data/login_interactive_inspection.json").write_text(json.dumps(all_results, indent=2))
    print("\nSaved to data/login_interactive_inspection.json")


if __name__ == "__main__":
    asyncio.run(main())
