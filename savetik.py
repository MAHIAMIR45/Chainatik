#!/usr/bin/env python3
"""
Savetik.co download engine — optimised for Render / headless environments.
"""

import os
import re
import time
import logging
import subprocess
from urllib.parse import urlparse, unquote

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PTimeout

# ── Configuration ──────────────────────────────────────────────────────────

SAVETIK_URL = os.getenv("SAVETIK_URL", "https://savetik.co/en2")
DEFAULT_TIMEOUT = 90_000
NAVIGATION_TIMEOUT = 120_000
DOWNLOAD_TIMEOUT = 180_000

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# On Render, Playwright stores browsers under /opt/render/project/.cache
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/opt/render/project/.cache/ms-playwright")

# ── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("savetik")

# ── Helpers ────────────────────────────────────────────────────────────────

def sanitise_filename(url: str) -> str:
    name = os.path.basename(urlparse(url).path)
    if not name or name.strip() in ("", "/", "video"):
        name = re.sub(r"[^a-zA-Z0-9]", "_", url.split("?")[0])[-40:]
    name = re.sub(r"[^\w\-\.\(\) ]", "_", name)
    return name if name.endswith(".mp4") else name + ".mp4"


def extract_download_url(page, timeout: int = 60_000) -> str:
    log.info("Extracting download URL …")
    strategies = [
        "a[download], a.download-btn, a[href*='.mp4'], a[class*='download'], a.btn-success",
        "video source[src]",
        "video[src]",
    ]
    for sel in strategies:
        try:
            el = page.wait_for_selector(sel, timeout=15_000)
            if el:
                href = el.get_attribute("href") or el.get_attribute("src")
                if href:
                    log.info("Download URL found: %s", href[:80])
                    return href
        except PTimeout:
            continue

    # Regex fallback
    content = page.content()
    for pat in [
        r'(https?://[^\s"\']+\.mp4[^\s"\']*)',
        r'(https?://[^\s"\']+/download[^\s"\']+)',
    ]:
        m = re.search(pat, content)
        if m:
            url = m.group(1).rstrip("'\"")
            log.info("Regex-extracted URL: %s", url[:80])
            return url

    raise RuntimeError("Could not extract download URL")


def _new_page(context):
    page = context.new_page()
    page.set_default_timeout(DEFAULT_TIMEOUT)
    return page


def _create_browser(playwright):
    return playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--single-process",
        ],
    )


def _create_context(browser):
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        accept_downloads=True,
    )
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
    """)
    return context


# ── Public download function (called from Flask) ───────────────────────────

def download_video(douyin_url: str, output_dir: str = "/tmp") -> dict:
    """
    Main entry point.  Returns dict:
        {"success": True,  "file": "/tmp/abc123.mp4", "filename": "abc123.mp4", "size": 12345}
        {"success": False, "error": "..."}
    """
    outname = sanitise_filename(douyin_url)
    outpath = os.path.join(output_dir, outname)

    with sync_playwright() as pw:
        browser = _create_browser(pw)
        try:
            context = _create_context(browser)
            page = _new_page(context)

            # ── 1. Navigate ────────────────────────────────────────────────
            log.info("Navigating to Savetik …")
            page.goto(SAVETIK_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT)
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PTimeout:
                pass
            page.wait_for_timeout(4000)

            # ── 2. Locate input ────────────────────────────────────────────
            input_sel = [
                "input[type='url']",
                "input[placeholder*='link' i]",
                "input[placeholder*='URL' i]",
                "input[name='url']",
                "input[name='link']",
                "textarea[placeholder*='link' i]",
            ]
            inp = None
            for s in input_sel:
                try:
                    inp = page.wait_for_selector(s, timeout=8_000)
                    if inp and inp.is_visible():
                        break
                except PTimeout:
                    continue
            if not inp:
                for el in page.query_selector_all("input[type='text'], input:not([type])"):
                    if el.is_visible():
                        inp = el
                        break
            if not inp:
                raise RuntimeError("Input field not found.  Page may have changed.")

            inp.click()
            inp.fill("")
            page.wait_for_timeout(500)
            inp.fill(douyin_url)
            page.wait_for_timeout(1500)

            # ── 3. Click download button ───────────────────────────────────
            btn_sel = [
                "button[type='submit']",
                "button:has-text('Download')",
                "button:has-text('Search')",
                "button:has-text('Start')",
                "input[type='submit']",
                "[class*='btn']:has-text('Download')",
            ]
            btn = None
            for s in btn_sel:
                try:
                    btn = page.wait_for_selector(s, timeout=5_000)
                    if btn and btn.is_visible():
                        break
                except PTimeout:
                    continue
            if not btn:
                btn = page.get_by_role("button").first
            if not btn:
                raise RuntimeError("Download button not found.")

            # ── 4. Try native Playwright download first ────────────────────
            try:
                with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as di:
                    btn.click()
                download = di.value
                fname = download.suggested_filename or outname
                fpath = os.path.join(output_dir, fname)
                download.save_as(fpath)
                size = os.path.getsize(fpath)
                log.info("Native download OK: %s (%d bytes)", fpath, size)
                browser.close()
                return {"success": True, "file": fpath, "filename": fname, "size": size}
            except (PTimeout, Exception) as e:
                log.warning("Native download failed (%s).  Trying requests-based.", e)

            # ── 5. Fallback: extract URL and download via requests ─────────
            video_url = extract_download_url(page)
            browser.close()

            headers = {"User-Agent": USER_AGENT, "Referer": SAVETIK_URL}
            resp = requests.get(video_url, headers=headers, stream=True, timeout=180)
            resp.raise_for_status()

            total = 0
            with open(outpath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)

            log.info("Requests download OK: %s (%d bytes)", outpath, total)
            return {"success": True, "file": outpath, "filename": outname, "size": total}

        except Exception as e:
            browser.close()
            log.error("Download failed: %s", e)
            return {"success": False, "error": str(e)}
