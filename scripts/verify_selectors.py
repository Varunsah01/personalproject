#!/usr/bin/env python3
"""Selector verification script for LinkedIn, Wellfound, and Cutshort.

Run with: python scripts/verify_selectors.py [linkedin|wellfound|cutshort]

Opens a NON-HEADLESS browser on each platform, navigates to the key pages
(login, search, job detail, apply flow), and reports which CSS selectors from
the platform module match live DOM elements. For misses, dumps a short HTML
snippet from the relevant container so the correct selector can be found.

Requires .env credentials to navigate past login pages. Screenshots are saved
to data/ for reference.

Does NOT click Apply — inspection only.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from textwrap import shorten

from dotenv import load_dotenv
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

load_dotenv()


# ── Helpers ────────────────────────────────────────────────────────────────

async def check(page, selector: str) -> tuple[bool, str]:
    """Return (found, outer_html_of_first_match)."""
    try:
        els = await page.query_selector_all(selector)
        if not els:
            return False, ""
        html = await els[0].evaluate("el => el.outerHTML")
        return True, shorten(html, width=280, placeholder="…")
    except Exception as exc:
        return False, f"[err: {exc}]"


def label(found: bool) -> str:
    return " OK " if found else "FAIL"


async def dump_container(page, js_expr: str, width: int = 800) -> str:
    """Evaluate a JS expression on the page and return a truncated result."""
    try:
        html = await page.evaluate(js_expr)
        return shorten(str(html or ""), width=width, placeholder="…")
    except Exception as exc:
        return f"[err: {exc}]"


async def try_alternatives(page, candidates: list[str]) -> str | None:
    """Return the first candidate selector that matches, or None."""
    for sel in candidates:
        found, _ = await check(page, sel)
        if found:
            return sel
    return None


async def wait_for_login(page, selectors: list[str], timeout: int = 15_000) -> bool:
    """Wait for any of the selectors to appear (post-login check)."""
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, timeout=timeout)
            return True
        except PlaywrightTimeout:
            continue
    return False


# ── LinkedIn ──────────────────────────────────────────────────────────────

async def verify_linkedin(pw, email: str, password: str) -> dict:
    print("\n" + "=" * 60)
    print("LINKEDIN")
    print("=" * 60)

    browser = await pw.chromium.launch(headless=False, slow_mo=100)
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = await ctx.new_page()
    results: dict[str, dict] = {}

    def record(name: str, sel: str, found: bool, html: str = "", note: str = "") -> None:
        results[name] = {"selector": sel, "found": found, "html": html, "note": note}
        print(f"  [{label(found)}] {name:40s}  {sel}")
        if note:
            print(f"         note: {note}")
        if not found and html:
            print(f"         dom:  {html}")

    # ── 1. Login page ──────────────────────────────────────────────────
    print("\n[1/5] Login page")
    await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
    await asyncio.sleep(2)

    for name, sel in [
        ("SEL_LOGIN_EMAIL",    "input#username"),
        ("SEL_LOGIN_PASSWORD", "input#password"),
        ("SEL_LOGIN_SUBMIT",   "button[type='submit']"),
        ("SEL_HUMAN_CHECK",    "div#challenge"),
    ]:
        found, html = await check(page, sel)
        note = ""
        if not found:
            # Dump the form so we can spot the right selector
            note = await dump_container(page, "document.querySelector('form')?.outerHTML || 'no form'", 600)
        record(name, sel, found, html, note)

    # ── 2. Login ───────────────────────────────────────────────────────
    if not email or not password:
        print("\n  [SKIP] No credentials in .env — skipping post-login selectors")
        await ctx.close()
        await browser.close()
        return results

    print("\n[2/5] Logging in …")
    try:
        await page.fill("input#username", email)
        await page.fill("input#password", password)
        await page.click("button[type='submit']")
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        print("  Login timed out")
        await ctx.close()
        await browser.close()
        return results
    await asyncio.sleep(3)
    await page.screenshot(path="data/linkedin_post_login.png")
    print(f"  URL after login: {page.url}")

    # SEL_LOGIN_SUCCESS — try current selector then alternatives
    sel = "div.feed-identity-module"
    found, html = await check(page, sel)
    if not found:
        alt = await try_alternatives(page, [
            "div.global-nav__me",
            "button[data-control-name='nav.settings']",
            "img.global-nav__me-photo",
            "span[data-anonymize='person-name']",
            "div[data-test-id='nav-account-icon']",
            "li.global-nav__primary-item--active",
            "nav.global-nav",
        ])
        note = f"ALTERNATIVE: {alt}" if alt else "NOT FOUND — dump nav: " + await dump_container(
            page, "document.querySelector('nav')?.outerHTML || 'no nav'", 600
        )
        record("SEL_LOGIN_SUCCESS", sel, found, html, note)
    else:
        record("SEL_LOGIN_SUCCESS", sel, found, html)

    # ── 3. Search results ──────────────────────────────────────────────
    print("\n[3/5] Search results page")
    await page.goto(
        "https://www.linkedin.com/jobs/search/"
        "?keywords=growth+manager&location=Delhi+NCR"
        "&f_AL=true&f_TPR=r604800&sortBy=DD",
        wait_until="domcontentloaded",
    )
    await asyncio.sleep(4)
    await page.screenshot(path="data/linkedin_search.png")

    for name, sel in [
        ("SEL_JOB_CARD",     "div.job-card-container"),
        ("SEL_JOB_TITLE",    "a.job-card-list__title"),
        ("SEL_JOB_COMPANY",  "span.job-card-container__primary-description"),
        ("SEL_JOB_LOCATION", "li.job-card-container__metadata-item"),
        ("SEL_JOB_SNIPPET",  "div.job-card-list__description"),
        ("SEL_NEXT_PAGE",    "button[aria-label='Next']"),
    ]:
        found, html = await check(page, sel)
        note = ""
        if not found:
            # Dump the first plausible job-card-like element
            note = await dump_container(
                page,
                """(() => {
                    const candidates = [
                        ...document.querySelectorAll('li[data-occludable-job-id]'),
                        ...document.querySelectorAll('[class*="job-card"]'),
                        ...document.querySelectorAll('[class*="jobs-search"]'),
                    ];
                    return candidates[0]?.outerHTML || document.querySelector('.jobs-search-results-list')?.innerHTML?.slice(0, 800) || 'nothing found';
                })()""",
                600,
            )
        record(name, sel, found, html, note)

    # ── 4. Job detail panel ────────────────────────────────────────────
    print("\n[4/5] Job detail panel")
    # Click the first job card if available
    card_el = await page.query_selector(
        "div.job-card-container, li[data-occludable-job-id], "
        "[class*='job-card-list__entity']"
    )
    if card_el:
        try:
            await card_el.click()
            await asyncio.sleep(3)
            await page.screenshot(path="data/linkedin_job_detail.png")
        except Exception as exc:
            print(f"  Could not click card: {exc}")

    for name, sel in [
        ("SEL_EASY_APPLY_BUTTON",    "button.jobs-apply-button"),
        ("SEL_EXTERNAL_APPLY_BUTTON","button.jobs-apply-button--external"),
        ("SEL_ALREADY_APPLIED",      "span.artdeco-inline-feedback"),
        ("SEL_JD_EXPERIENCE",        "span.job-criteria__text"),
        ("SEL_PROFILE_MENU",         "button.global-nav__primary-link--me"),
    ]:
        found, html = await check(page, sel)
        note = ""
        if not found and name == "SEL_EASY_APPLY_BUTTON":
            note = await dump_container(
                page,
                "document.querySelector('.jobs-apply-button, [class*=\"apply\"]')?.outerHTML || "
                "document.querySelector('.jobs-details-top-card__container-actions')?.innerHTML?.slice(0,600) || "
                "'no apply section'",
                600,
            )
        record(name, sel, found, html, note)

    # ── 5. Try to open Easy Apply modal ───────────────────────────────
    print("\n[5/5] Easy Apply modal (attempting to open …)")
    easy_btn = await page.query_selector("button.jobs-apply-button")
    if easy_btn:
        btn_text = await easy_btn.inner_text()
        print(f"  Button text: {btn_text!r}")
        if "easy apply" in btn_text.lower():
            try:
                await easy_btn.click()
                await asyncio.sleep(3)
                await page.screenshot(path="data/linkedin_modal.png")
            except Exception as exc:
                print(f"  Click failed: {exc}")

            for name, sel in [
                ("SEL_MODAL_CONTAINER",      "div.jobs-easy-apply-modal"),
                ("SEL_MODAL_STEP_INDICATOR", "span.jobs-easy-apply-modal__page-count"),
                ("SEL_MODAL_NEXT_BUTTON",    "button[aria-label='Continue to next step']"),
                ("SEL_MODAL_REVIEW_BUTTON",  "button[aria-label='Review your application']"),
                ("SEL_MODAL_SUBMIT_BUTTON",  "button[aria-label='Submit application']"),
                ("SEL_MODAL_CLOSE_BUTTON",   "button[aria-label='Dismiss']"),
                ("SEL_MODAL_DISCARD_BUTTON", "button[data-test-modal-close-btn]"),
                ("SEL_MODAL_INPUT_TEXT",     "input[type='text']"),
                ("SEL_MODAL_INPUT_SELECT",   "select"),
                ("SEL_MODAL_TEXTAREA",       "textarea"),
                ("SEL_MODAL_QUESTION_TEXT",  "span.fb-form-element-label"),
                ("SEL_RESUME_UPLOAD",        "input[type='file']"),
                ("SEL_MODAL_ERROR",          "div.artdeco-inline-feedback--error"),
            ]:
                found, html = await check(page, sel)
                note = ""
                if not found and name == "SEL_MODAL_CONTAINER":
                    note = await dump_container(
                        page,
                        "document.querySelector('[role=\"dialog\"], [class*=\"modal\"]')?.outerHTML?.slice(0,600) || 'no modal'",
                        600,
                    )
                record(name, sel, found, html, note)

            # Close modal
            close_btn = await page.query_selector("button[aria-label='Dismiss']")
            if close_btn:
                await close_btn.click()
                await asyncio.sleep(1)
    else:
        print("  No Easy Apply button found — skipping modal checks")

    # Logout link (just check nav area)
    found, html = await check(page, "a[href*='logout']")
    record("SEL_LOGOUT_LINK", "a[href*='logout']", found, html)

    await ctx.close()
    await browser.close()
    return results


# ── Wellfound ─────────────────────────────────────────────────────────────

async def verify_wellfound(pw, email: str, password: str) -> dict:
    print("\n" + "=" * 60)
    print("WELLFOUND")
    print("=" * 60)

    browser = await pw.chromium.launch(headless=False, slow_mo=100)
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = await ctx.new_page()
    results: dict[str, dict] = {}

    def record(name, sel, found, html="", note=""):
        results[name] = {"selector": sel, "found": found, "html": html, "note": note}
        print(f"  [{label(found)}] {name:40s}  {sel}")
        if note:
            print(f"         note: {note}")
        if not found and html:
            print(f"         dom:  {html}")

    # ── 1. Login page ──────────────────────────────────────────────────
    print("\n[1/4] Login page")
    await page.goto("https://wellfound.com/login", wait_until="domcontentloaded")
    await asyncio.sleep(3)
    await page.screenshot(path="data/wellfound_login.png")

    for name, sel in [
        ("SEL_LOGIN_EMAIL",    "input[name='user[email]']"),
        ("SEL_LOGIN_PASSWORD", "input[name='user[password]']"),
        ("SEL_LOGIN_SUBMIT",   "button[type='submit']"),
    ]:
        found, html = await check(page, sel)
        note = ""
        if not found:
            note = await dump_container(page, "document.querySelector('form')?.outerHTML || 'no form'", 600)
        record(name, sel, found, html, note)

    if not email or not password:
        print("\n  [SKIP] No credentials — skipping post-login")
        await ctx.close()
        await browser.close()
        return results

    # ── 2. Login ───────────────────────────────────────────────────────
    print("\n[2/4] Logging in …")
    try:
        # Try both selector variants
        email_sel = "input[name='user[email]']" if await page.query_selector("input[name='user[email]']") else "input[type='email']"
        pass_sel = "input[name='user[password]']" if await page.query_selector("input[name='user[password]']") else "input[type='password']"
        await page.fill(email_sel, email)
        await page.fill(pass_sel, password)
        await page.click("button[type='submit']")
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        print("  Login timed out")
        await ctx.close()
        await browser.close()
        return results
    await asyncio.sleep(3)
    await page.screenshot(path="data/wellfound_post_login.png")
    print(f"  URL after login: {page.url}")

    sel = "a[href*='/me']"
    found, html = await check(page, sel)
    if not found:
        alt = await try_alternatives(page, [
            "a[href*='/profile']",
            "img[alt*='Profile']",
            "div[data-test='user-menu']",
            "button[data-test='user-menu-button']",
            "header nav a[href*='/dashboard']",
            "[class*='userNav']",
            "[class*='nav__user']",
        ])
        note = f"ALTERNATIVE: {alt}" if alt else "NOT FOUND — " + await dump_container(
            page, "document.querySelector('header')?.innerHTML?.slice(0,600) || 'no header'", 600
        )
        record("SEL_LOGIN_SUCCESS", sel, found, html, note)
    else:
        record("SEL_LOGIN_SUCCESS", sel, found, html)

    # ── 3. Profile completeness ────────────────────────────────────────
    print("\n[3/4] Profile page (completeness check)")
    await page.goto("https://wellfound.com/profile/edit", wait_until="domcontentloaded")
    await asyncio.sleep(3)
    await page.screenshot(path="data/wellfound_profile.png")

    sel = "div.profile-completeness"
    found, html = await check(page, sel)
    if not found:
        alt = await try_alternatives(page, [
            "[aria-label*='omplete']",
            "[class*='completeness']",
            "[class*='strength']",
            "[class*='progress']",
            "[role='progressbar']",
            "[data-test*='completeness']",
            "[data-test*='profile-strength']",
        ])
        note = f"ALTERNATIVE: {alt}" if alt else "NOT FOUND — " + await dump_container(
            page,
            """(() => {
                const cands = [...document.querySelectorAll('[class*="progress"], [class*="complete"], [role="progressbar"]')];
                return cands.slice(0,3).map(el => el.outerHTML.slice(0,200)).join('\\n') || 'nothing found';
            })()""",
            600,
        )
        record("SEL_PROFILE_COMPLETENESS", sel, found, html, note)
    else:
        record("SEL_PROFILE_COMPLETENESS", sel, found, html)

    # ── 4. Search results ──────────────────────────────────────────────
    print("\n[4/4] Search results")
    await page.goto("https://wellfound.com/jobs?q=growth+manager&l=India", wait_until="domcontentloaded")
    await asyncio.sleep(4)
    await page.screenshot(path="data/wellfound_search.png")

    for name, sel in [
        ("SEL_JOB_CARD",     "div[data-test='StartupResult']"),
        ("SEL_JOB_TITLE",    "a[data-test='job-title']"),
        ("SEL_JOB_COMPANY",  "a[data-test='startup-link']"),
        ("SEL_JOB_LOCATION", "span[data-test='location']"),
        ("SEL_JOB_EXPERIENCE","span[data-test='job-type']"),
        ("SEL_JOB_SNIPPET",  "p[data-test='job-description']"),
        ("SEL_NEXT_PAGE",    "a[rel='next']"),
        ("SEL_APPLY_BUTTON", "button[data-test='apply-button']"),
        ("SEL_EXTERNAL_APPLY_INDICATOR", "a[data-test='external-apply']"),
        ("SEL_ALREADY_APPLIED","span[data-test='applied-badge']"),
        ("SEL_APPLY_MODAL",  "div[data-test='apply-modal']"),
        ("SEL_MODAL_CLOSE",  "button[data-test='close-modal']"),
        ("SEL_WHY_TEXTAREA", "textarea[name*='why'], textarea[placeholder*='why'], textarea[data-test*='why']"),
        ("SEL_MODAL_SUBMIT", "button[data-test='submit-application']"),
        ("SEL_APPLY_SUCCESS","div[data-test='application-sent']"),
    ]:
        found, html = await check(page, sel)
        note = ""
        if not found and name == "SEL_JOB_CARD":
            note = await dump_container(
                page,
                """(() => {
                    const cands = [
                        ...document.querySelectorAll('[data-test]'),
                        ...document.querySelectorAll('[class*="JobResult"], [class*="job-result"]'),
                    ];
                    return cands.slice(0,5).map(el => el.getAttribute('data-test') + ': ' + el.tagName.toLowerCase() + '.' + el.className.slice(0,50)).join('\\n') || 'no data-test elements';
                })()""",
                800,
            )
        record(name, sel, found, html, note)

    await ctx.close()
    await browser.close()
    return results


# ── Cutshort ──────────────────────────────────────────────────────────────

async def verify_cutshort(pw, email: str, password: str) -> dict:
    print("\n" + "=" * 60)
    print("CUTSHORT")
    print("=" * 60)

    browser = await pw.chromium.launch(headless=False, slow_mo=100)
    ctx = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    )
    page = await ctx.new_page()
    results: dict[str, dict] = {}

    def record(name, sel, found, html="", note=""):
        results[name] = {"selector": sel, "found": found, "html": html, "note": note}
        print(f"  [{label(found)}] {name:40s}  {sel}")
        if note:
            print(f"         note: {note}")
        if not found and html:
            print(f"         dom:  {html}")

    # ── 1. Login page ──────────────────────────────────────────────────
    print("\n[1/3] Login page")
    await page.goto("https://cutshort.io/login", wait_until="domcontentloaded")
    await asyncio.sleep(3)
    await page.screenshot(path="data/cutshort_login.png")

    for name, sel in [
        ("SEL_LOGIN_EMAIL",    "input[type='email']"),
        ("SEL_LOGIN_PASSWORD", "input[type='password']"),
        ("SEL_LOGIN_SUBMIT",   "button[type='submit']"),
    ]:
        found, html = await check(page, sel)
        note = ""
        if not found:
            note = await dump_container(page, "document.querySelector('form')?.outerHTML || 'no form'", 600)
        record(name, sel, found, html, note)

    if not email or not password:
        print("\n  [SKIP] No credentials — skipping post-login")
        await ctx.close()
        await browser.close()
        return results

    # ── 2. Login ───────────────────────────────────────────────────────
    print("\n[2/3] Logging in …")
    try:
        await page.fill("input[type='email']", email)
        await page.fill("input[type='password']", password)
        await page.click("button[type='submit']")
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        print("  Login timed out")
        await ctx.close()
        await browser.close()
        return results
    await asyncio.sleep(3)
    await page.screenshot(path="data/cutshort_post_login.png")
    print(f"  URL after login: {page.url}")

    sel = "a[href*='/profile']"
    found, html = await check(page, sel)
    if not found:
        alt = await try_alternatives(page, [
            "a[href*='/me']",
            "a[href*='/candidate']",
            "[class*='avatar']",
            "[class*='userAvatar']",
            "img[alt*='profile']",
            "img[alt*='avatar']",
            "[data-test='user-nav']",
        ])
        note = f"ALTERNATIVE: {alt}" if alt else "NOT FOUND — " + await dump_container(
            page, "document.querySelector('nav, header')?.innerHTML?.slice(0,600) || 'no nav'", 600
        )
        record("SEL_LOGIN_SUCCESS", sel, found, html, note)
    else:
        record("SEL_LOGIN_SUCCESS", sel, found, html)

    # ── 3. Search results (tag-based) ──────────────────────────────────
    print("\n[3/3] Search results")
    await page.goto(
        "https://cutshort.io/jobs?tags[]=growth-manager&locations[]=Delhi",
        wait_until="domcontentloaded",
    )
    await asyncio.sleep(4)
    await page.screenshot(path="data/cutshort_search.png")

    for name, sel in [
        ("SEL_JOB_CARD",     "div[data-test='job-card']"),
        ("SEL_JOB_TITLE",    "a[data-test='job-title']"),
        ("SEL_JOB_COMPANY",  "span[data-test='company-name']"),
        ("SEL_JOB_LOCATION", "span[data-test='job-location']"),
        ("SEL_JOB_EXPERIENCE","span[data-test='experience']"),
        ("SEL_JOB_SNIPPET",  "p[data-test='job-description']"),
        ("SEL_NEXT_PAGE",    "a[rel='next'], button[data-test='next-page']"),
        ("SEL_APPLY_BUTTON", "button[data-test='apply-button'], a[data-test='apply-button']"),
        ("SEL_ALREADY_APPLIED","span[data-test='applied-status']"),
        ("SEL_APPLY_SUCCESS","div[data-test='application-success']"),
        ("SEL_APPLY_FORM",   "form[data-test='apply-form'], div[data-test='apply-modal']"),
    ]:
        found, html = await check(page, sel)
        note = ""
        if not found and name == "SEL_JOB_CARD":
            note = await dump_container(
                page,
                """(() => {
                    const cands = [
                        ...document.querySelectorAll('[data-test]'),
                        ...document.querySelectorAll('[class*="JobCard"], [class*="job-card"]'),
                    ];
                    const seen = new Set();
                    return cands.slice(0, 10)
                        .map(el => (el.getAttribute('data-test') || '') + '|' + el.tagName + '.' + el.className.replace(/\\s+/g,' ').slice(0,60))
                        .filter(s => { if(seen.has(s)) return false; seen.add(s); return true; })
                        .join('\\n') || 'no data-test elements / no job-card classes found';
                })()""",
                800,
            )
        record(name, sel, found, html, note)

    await ctx.close()
    await browser.close()
    return results


# ── Main ──────────────────────────────────────────────────────────────────

async def main() -> None:
    platform_filter = sys.argv[1].lower() if len(sys.argv) > 1 else None

    li_email = os.getenv("LINKEDIN_EMAIL", "")
    li_pass = os.getenv("LINKEDIN_PASSWORD", "")
    wf_email = os.getenv("WELLFOUND_EMAIL", "")
    wf_pass = os.getenv("WELLFOUND_PASSWORD", "")
    cs_email = os.getenv("CUTSHORT_EMAIL", "")
    cs_pass = os.getenv("CUTSHORT_PASSWORD", "")

    Path("data").mkdir(exist_ok=True)
    all_results: dict[str, dict] = {}

    async with async_playwright() as pw:
        if not platform_filter or platform_filter == "linkedin":
            all_results["linkedin"] = await verify_linkedin(pw, li_email, li_pass)

        if not platform_filter or platform_filter == "wellfound":
            all_results["wellfound"] = await verify_wellfound(pw, wf_email, wf_pass)

        if not platform_filter or platform_filter == "cutshort":
            all_results["cutshort"] = await verify_cutshort(pw, cs_email, cs_pass)

    # Save JSON for reference
    out = Path("data/selector_verification_results.json")
    out.write_text(json.dumps(all_results, indent=2))

    # Print summary table
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    total_ok = total_fail = 0
    for platform, res in all_results.items():
        ok = sum(1 for r in res.values() if r["found"])
        fail = len(res) - ok
        total_ok += ok
        total_fail += fail
        print(f"\n{platform.upper()}:  {ok} OK, {fail} FAIL")
        for name, r in res.items():
            if not r["found"]:
                print(f"  FAIL  {name}: {r['selector']}")
                if r.get("note"):
                    print(f"        {r['note'][:200]}")

    print(f"\nTotal: {total_ok} OK, {total_fail} FAIL")
    print(f"Results saved to {out}")


if __name__ == "__main__":
    asyncio.run(main())
