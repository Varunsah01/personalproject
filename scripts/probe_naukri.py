#!/usr/bin/env python3
"""Naukri DOM probe — diagnose why selectors miss job cards.

Reuses the same Playwright context as platforms/naukri.py (same UA,
stealth, persistent profile).  Logs in, navigates to a search URL,
waits for the page to fully render, saves HTML + screenshot + DOM dump.

Usage:
    python scripts/probe_naukri.py [iteration_number]
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

# Add project root to path so we can import core/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.browser import create_browser_context

load_dotenv()

SEARCH_URL = "https://www.naukri.com/growth-manager-jobs-in-delhi-ncr"
ITER = int(sys.argv[1]) if len(sys.argv) > 1 else 1


async def main() -> None:
    Path("data").mkdir(exist_ok=True)
    email = os.getenv("NAUKRI_EMAIL", "")
    password = os.getenv("NAUKRI_PASSWORD", "")
    print(f"[probe] iteration={ITER}, email={'set' if email else 'MISSING'}")

    pw = await async_playwright().start()
    ctx = await create_browser_context(
        platform="naukri",
        headless=False,  # headful so we can screenshot
        playwright=pw,
    )
    page = await ctx.new_page()

    # ── Step 1: Navigate to login page and check session ─────────────
    print("[probe] Navigating to login page...")
    await page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded")
    await asyncio.sleep(2)

    # Check if already logged in via persistent session
    login_success_sel = "img.nI-gNb-icon-img"
    already_logged_in = False
    try:
        await page.wait_for_selector(login_success_sel, timeout=3_000)
        already_logged_in = True
        print("[probe] Already logged in via persistent session")
    except PlaywrightTimeout:
        print("[probe] Not logged in — attempting login...")

    if not already_logged_in:
        if not email or not password:
            print("[probe] FATAL: No credentials in .env, cannot log in")
            await ctx.close()
            await pw.stop()
            return

        try:
            await page.fill("input[placeholder*='Email']", email)
            await page.fill("input[type='password']", password)
            await page.click("button[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=15_000)
            await asyncio.sleep(2)
        except PlaywrightTimeout:
            print("[probe] Login timed out")

        # Check CAPTCHA
        captcha = await page.query_selector("iframe[src*='recaptcha']")
        if captcha:
            print("[probe] CAPTCHA DETECTED on login page — STOPPING")
            await page.screenshot(path=f"data/naukri_probe_iter{ITER}.png")
            await ctx.close()
            await pw.stop()
            return

        try:
            await page.wait_for_selector(login_success_sel, timeout=5_000)
            print("[probe] Login succeeded")
        except PlaywrightTimeout:
            print("[probe] Login may have failed — continuing to probe anyway")

    # ── Step 2: Navigate to search results ────────────────────────────
    print(f"[probe] Navigating to: {SEARCH_URL}")
    await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30_000)

    # Record URL after navigation (detect redirects)
    url_after_nav = page.url
    title_after_nav = await page.title()
    print(f"[probe] URL after domcontentloaded: {url_after_nav}")
    print(f"[probe] Title: {title_after_nav}")

    # ── Step 3: Check for anti-bot markers BEFORE waiting ─────────────
    antibot_check = await page.evaluate("""() => {
        const markers = [];
        const title = document.title.toLowerCase();
        if (title.includes('security check')) markers.push('title:security_check');
        if (title.includes('access denied')) markers.push('title:access_denied');

        const iframes = document.querySelectorAll('iframe[src]');
        for (const f of iframes) {
            const src = f.src.toLowerCase();
            if (src.includes('recaptcha')) markers.push('iframe:recaptcha');
            if (src.includes('hcaptcha')) markers.push('iframe:hcaptcha');
        }

        const text = (document.body?.innerText || '').toLowerCase();
        if (text.includes('verify you are human')) markers.push('text:verify_human');
        if (text.includes('are you a human')) markers.push('text:are_you_human');
        if (text.includes('blocked')) markers.push('text:blocked');

        // Check for login form (redirected to login wall)
        const loginForm = document.querySelector("input[placeholder*='Email']");
        if (loginForm) markers.push('element:login_form_visible');

        const bodyLen = (document.body?.innerHTML || '').length;
        if (bodyLen < 500) markers.push('body:suspiciously_short_' + bodyLen);

        return markers;
    }""")
    print(f"[probe] Anti-bot markers (pre-wait): {antibot_check}")

    # ── Step 4: Wait for network idle + extra time for React CSR ──────
    print("[probe] Waiting for networkidle...")
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        print("[probe] networkidle timed out (not fatal, continuing)")

    print("[probe] Extra 3s wait for late-rendering JS...")
    await asyncio.sleep(3)

    # ── Step 5: Try waiting for the job card selector explicitly ───────
    job_card_sel = "div.srp-jobtuple-wrapper"
    print(f"[probe] Waiting for job card selector: {job_card_sel}")
    try:
        await page.wait_for_selector(job_card_sel, timeout=10_000)
        print("[probe] Job card selector FOUND after explicit wait")
    except PlaywrightTimeout:
        print("[probe] Job card selector NOT FOUND even after 10s wait")

    # ── Step 6: Save HTML and screenshot ──────────────────────────────
    html_content = await page.content()
    html_path = Path(f"data/naukri_probe_iter{ITER}.html")
    html_path.write_text(html_content, encoding="utf-8")
    print(f"[probe] Saved HTML ({len(html_content)} chars) to {html_path}")

    screenshot_path = f"data/naukri_probe_iter{ITER}.png"
    await page.screenshot(path=screenshot_path, full_page=True)
    print(f"[probe] Saved screenshot to {screenshot_path}")

    # ── Step 7: DOM probe — find job-card-like elements ───────────────
    probe_results = await page.evaluate("""() => {
        const results = { job_divs: [], role_elements: [], heading_ago: [] };

        // Heuristic 1: elements with "job" in class or data-* attrs
        const allEls = document.querySelectorAll('*');
        for (const el of allEls) {
            const cls = el.className?.toString() || '';
            const hasJobClass = /job/i.test(cls);
            const hasJobData = [...el.attributes].some(
                a => a.name.startsWith('data-') && /job/i.test(a.name + a.value)
            );
            if ((hasJobClass || hasJobData) && el.children.length > 0) {
                results.job_divs.push({
                    tag: el.tagName,
                    class: cls.substring(0, 120),
                    dataAttrs: [...el.attributes]
                        .filter(a => a.name.startsWith('data-'))
                        .map(a => a.name + '=' + a.value.substring(0, 30)),
                    outerHTML: el.outerHTML.substring(0, 600),
                    childCount: el.children.length
                });
            }
            if (results.job_divs.length >= 15) break;
        }

        // Heuristic 2: role="article" or role="listitem"
        const roleEls = document.querySelectorAll('[role="article"], [role="listitem"]');
        for (const el of [...roleEls].slice(0, 5)) {
            results.role_elements.push({
                tag: el.tagName,
                class: (el.className?.toString() || '').substring(0, 80),
                role: el.getAttribute('role'),
                textSnippet: el.textContent?.trim()?.substring(0, 100)
            });
        }

        // Heuristic 3: elements containing heading + "ago" text (posted-date hint)
        const headings = document.querySelectorAll('h2, h3, h4');
        for (const h of [...headings].slice(0, 10)) {
            const parent = h.closest('div, article, li, section');
            if (parent && /ago|day|week|month/i.test(parent.textContent || '')) {
                results.heading_ago.push({
                    heading_tag: h.tagName,
                    heading_text: h.textContent?.trim()?.substring(0, 60),
                    parent_tag: parent.tagName,
                    parent_class: (parent.className?.toString() || '').substring(0, 80),
                    parent_snippet: parent.textContent?.trim()?.substring(0, 120)
                });
            }
        }

        // Also test each existing selector
        const selectors = {
            'job_card': 'div.srp-jobtuple-wrapper',
            'job_title': 'a.title',
            'job_company': 'a.comp-name',
            'job_location': 'span.loc-wrap',
            'job_experience': 'span.exp-wrap',
            'job_snippet': 'span.job-desc',
            'next_page': 'a.styles_btn-secondary__2AsIP',
        };
        results.existing_selectors = {};
        for (const [name, sel] of Object.entries(selectors)) {
            const els = document.querySelectorAll(sel);
            results.existing_selectors[name] = {
                count: els.length,
                first_text: els[0]?.textContent?.trim()?.substring(0, 60) || null
            };
        }

        return results;
    }""")

    print("\n[probe] === DOM PROBE RESULTS ===")
    print(f"\n[probe] Existing selector matches:")
    for name, info in probe_results.get("existing_selectors", {}).items():
        status = "OK" if info["count"] > 0 else "MISS"
        print(f"  [{status}] {name}: {info['count']} matches", end="")
        if info.get("first_text"):
            print(f"  (first: {info['first_text'][:40]})", end="")
        print()

    print(f"\n[probe] Job-class elements found: {len(probe_results.get('job_divs', []))}")
    for item in probe_results.get("job_divs", [])[:5]:
        print(f"  {item['tag']}.{item['class'][:60]}")
        if item.get("dataAttrs"):
            print(f"    data attrs: {item['dataAttrs']}")

    print(f"\n[probe] Role elements: {len(probe_results.get('role_elements', []))}")
    for item in probe_results.get("role_elements", [])[:3]:
        print(f"  {item['tag']} role={item['role']} class={item['class'][:40]}")

    print(f"\n[probe] Heading+ago elements: {len(probe_results.get('heading_ago', []))}")
    for item in probe_results.get("heading_ago", [])[:3]:
        print(f"  {item['heading_tag']}: {item['heading_text'][:40]}")
        print(f"    parent: {item['parent_tag']}.{item['parent_class'][:40]}")

    # Save full probe results
    probe_path = Path(f"data/naukri_probe_iter{ITER}_results.json")
    probe_path.write_text(json.dumps(probe_results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[probe] Full results saved to {probe_path}")

    # ── Step 8: Anti-bot check (post-wait) ────────────────────────────
    antibot_post = await page.evaluate("""() => {
        const markers = [];
        const title = document.title.toLowerCase();
        if (title.includes('security check')) markers.push('title:security_check');
        if (title.includes('access denied')) markers.push('title:access_denied');

        const iframes = document.querySelectorAll('iframe[src]');
        for (const f of iframes) {
            const src = f.src.toLowerCase();
            if (src.includes('recaptcha')) markers.push('iframe:recaptcha');
            if (src.includes('hcaptcha')) markers.push('iframe:hcaptcha');
        }

        const text = (document.body?.innerText || '').toLowerCase();
        if (text.includes('verify you are human')) markers.push('text:verify_human');
        if (text.includes('are you a human')) markers.push('text:are_you_human');

        return markers;
    }""")
    print(f"\n[probe] Anti-bot markers (post-wait): {antibot_post}")

    if antibot_post:
        print("[probe] *** ANTI-BOT DETECTION — see screenshot ***")

    # Cleanup
    await ctx.close()
    await pw.stop()
    print("[probe] Done.")


if __name__ == "__main__":
    asyncio.run(main())
