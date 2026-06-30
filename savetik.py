#!/usr/bin/env python3
"""
Savetik Douyin/TikTok Downloader — Core Engine
Optimised for Render headless environment with Playwright + Chromium.
"""

import os
import re
import time
import logging
from urllib.parse import urlparse, unquote

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PTimeout

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

SAVETIK_URL = os.getenv("SAVETIK_URL", "https://savetik.co/en2")
DEFAULT_TIMEOUT = 90_000       # 90 seconds
NAVIGATION_TIMEOUT = 120_000   # 120 seconds
DOWNLOAD_TIMEOUT = 180_000     # 180 seconds

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# Temp directory for downloaded files (Render /tmp is writable)
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", "/tmp/savetik_downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ═══════════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("savetik")

# ═══════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def sanitise_filename(url: str) -> str:
    """Derive a safe .mp4 filename from a URL."""
    name = os.path.basename(urlparse(url).path)
    if not name or name.strip() in ("", "/", "video"):
        # Use last 40 chars of sanitised URL
        name = re.sub(r"[^a-zA-Z0-9]", "_", url.split("?")[0])[-40:]
    name = re.sub(r"[^\w\-\.\(\) ]", "_", name)
    return name if name.endswith(".mp4") else name + ".mp4"


def extract_download_url(page, timeout: int = 60_000) -> str:
    """
    After Savetik processes the video, extract the direct MP4 download URL.
    Tries multiple strategies in order of reliability.
    """
    log.info("Extracting download URL from page ...")

    # Strategy 1: <a> tags with download attributes or .mp4 hrefs
    for sel in [
        "a[download]",
        "a.download-btn",
        "a.download-button",
        "a[href*='.mp4']",
        "a[class*='download']",
        "a.btn-success",
        "a[href*='/download']",
    ]:
        try:
            el = page.wait_for_selector(sel, timeout=10_000)
            if el:
                href = el.get_attribute("href")
                if href:
                    log.info("Strategy 1 - Found <a> download link: %s", href[:80])
                    return href
        except PTimeout:
            continue

    # Strategy 2: <video> tag with <source> child
    try:
        source = page.wait_for_selector("video source[src]", timeout=10_000)
        src = source.get_attribute("src")
        if src:
            log.info("Strategy 2 - Found <video><source> URL: %s", src[:80])
            return src
    except PTimeout:
        pass

    # Strategy 3: <video> tag with direct src attribute
    try:
        video = page.wait_for_selector("video[src]", timeout=10_000)
        src = video.get_attribute("src")
        if src:
            log.info("Strategy 3 - Found <video[src]> URL: %s", src[:80])
            return src
    except PTimeout:
        pass

    # Strategy 4: Grab all <a> links with .mp4
    mp4_links = page.query_selector_all("a[href*='.mp4']")
    for link in mp4_links:
        href = link.get_attribute("href")
        if href:
            log.info("Strategy 4 - Found .mp4 link: %s", href[:80])
            return href

    # Strategy 5: Regex scan of full page source
    log.info("Strategy 5 - Scanning page source with regex ...")
    content = page.content()
    patterns = [
        r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/download[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/video/[^\s"\'<>]+)',
    ]
    for pat in patterns:
        match = re.search(pat, content)
        if match:
            url = match.group(1).rstrip("'\"<>")
            log.info("Regex matched URL: %s", url[:80])
            return url

    # If nothing worked, raise error
    raise RuntimeError(
        "Could not extract download URL. The site structure may have changed."
    )


# ═══════════════════════════════════════════════════════════════════════════
# PLAYWRIGHT BROWSER MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

def _create_browser(playwright):
    """Launch headless Chromium with Render-optimised flags."""
    return playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--single-process",
            "--disable-software-rasterizer",
            "--no-zygote",
            "--disable-extensions",
            "--disable-sync",
            "--no-first-run",
            "--disable-background-networking",
            "--disable-default-apps",
            "--mute-audio",
            "--hide-scrollbars",
            "--disable-breakpad",
            "--disable-component-update",
        ],
    )


def _create_context(browser):
    """Create a stealthy browser context with anti-detection measures."""
    context = browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1280, "height": 800},
        locale="en-US",
        accept_downloads=True,
    )
    # Remove Playwright/webdriver traces
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {} };
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5]
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });
    """)
    return context


def _new_page(context):
    """Create a new page with default timeout."""
    page = context.new_page()
    page.set_default_timeout(DEFAULT_TIMEOUT)
    return page


# ═══════════════════════════════════════════════════════════════════════════
# MAIN DOWNLOAD FUNCTION
# ═══════════════════════════════════════════════════════════════════════════

def download_video(douyin_url: str, output_dir: str = None) -> dict:
    """
    Main download function.

    Parameters
    ----------
    douyin_url : str
        A Douyin or TikTok video URL.
    output_dir : str, optional
        Directory to save the file. Defaults to DOWNLOAD_DIR.

    Returns
    -------
    dict
        {"success": True, "file": "/path/to/video.mp4",
         "filename": "video.mp4", "size": 123456}
        OR
        {"success": False, "error": "Error message"}
    """
    if output_dir is None:
        output_dir = DOWNLOAD_DIR

    os.makedirs(output_dir, exist_ok=True)
    outname = sanitise_filename(douyin_url)
    outpath = os.path.join(output_dir, outname)

    log.info("=" * 60)
    log.info("Starting download for: %s", douyin_url)
    log.info("Output file: %s", outpath)
    log.info("=" * 60)

    with sync_playwright() as pw:
        browser = _create_browser(pw)
        try:
            context = _create_context(browser)
            page = _new_page(context)

            # ── 1. Navigate to Savetik.co ─────────────────────────────
            log.info("Navigating to %s ...", SAVETIK_URL)
            page.goto(
                SAVETIK_URL,
                wait_until="domcontentloaded",
                timeout=NAVIGATION_TIMEOUT,
            )

            # Wait for page to fully settle (Cloudflare bypass, JS rendering)
            try:
                page.wait_for_load_state("networkidle", timeout=30_000)
            except PTimeout:
                log.warning("networkidle timeout - continuing anyway")
            page.wait_for_timeout(4000)  # Extra buffer for dynamic widgets

            log.info("Page loaded. URL: %s | Title: %s", page.url, page.title())

            # ── 2. Locate the URL input field ─────────────────────────
            log.info("Looking for URL input field ...")
            input_selectors = [
                "input[type='url']",
                "input[placeholder*='link' i]",
                "input[placeholder*='URL' i]",
                "input[placeholder*='Paste' i]",
                "input[name='url']",
                "input[name='link']",
                "input[id*='url']",
                "input[id*='link']",
                "input[class*='url']",
                "input[class*='input']",
                "textarea[placeholder*='link' i]",
                "textarea[placeholder*='URL' i]",
            ]

            input_el = None
            for sel in input_selectors:
                try:
                    el = page.wait_for_selector(sel, timeout=8_000)
                    if el and el.is_visible():
                        input_el = el
                        log.info("Found input with selector: %s", sel)
                        break
                except PTimeout:
                    continue

            if not input_el:
                # Fallback: any visible text input
                for el in page.query_selector_all(
                    "input[type='text'], input:not([type])"
                ):
                    if el.is_visible():
                        input_el = el
                        log.info("Found fallback input element")
                        break

            if not input_el:
                raise RuntimeError("Could not find URL input field on Savetik.co")

            # ── 3. Fill the URL ─────────────────────────────────────────
            log.info("Filling URL input ...")
            input_el.click()
            input_el.fill("")
            page.wait_for_timeout(500)
            input_el.fill(douyin_url)
            page.wait_for_timeout(1500)

            # ── 4. Click the Download button ────────────────────────────
            log.info("Looking for download/submit button ...")
            button_selectors = [
                "button[type='submit']",
                "button:has-text('Download')",
                "button:has-text('Search')",
                "button:has-text('Start')",
                "button:has-text('Get')",
                "input[type='submit']",
                "input[value*='Download' i]",
                "input[value*='Search' i]",
                "[class*='btn']:has-text('Download')",
                "[class*='btn']:has-text('Search')",
                "[class*='btn']:has-text('Start')",
                "a[class*='btn']:has-text('Download')",
            ]

            btn = None
            for sel in button_selectors:
                try:
                    el = page.wait_for_selector(sel, timeout=5_000)
                    if el and el.is_visible():
                        btn = el
                        log.info("Found button with selector: %s", sel)
                        break
                except PTimeout:
                    continue

            if not btn:
                # Try by role
                btn = page.get_by_role("button", name="Download").first
                if not btn:
                    btn = page.get_by_role("button").first

            if not btn:
                raise RuntimeError("Could not find download button on Savetik.co")

            # ── 5. Attempt native Playwright download ──────────────────
            log.info("Attempting native Playwright download ...")
            try:
                with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as download_info:
                    btn.click()
                    log.info("Button clicked, waiting for download ...")

                download = download_info.value
                suggested_name = download.suggested_filename or outname
                final_path = os.path.join(output_dir, suggested_name)
                download.save_as(final_path)
                file_size = os.path.getsize(final_path)

                log.info("✓ Native download successful!")
                log.info("  File: %s", final_path)
                log.info("  Size: %d bytes", file_size)

                browser.close()
                return {
                    "success": True,
                    "file": final_path,
                    "filename": suggested_name,
                    "size": file_size,
                }

            except (PTimeout, Exception) as e:
                log.warning(
                    "Native download failed: %s. Falling back to requests method ...", e
                )

            # ── 6. Fallback: extract URL, download via requests ────────
            log.info("Using requests fallback method ...")
            video_url = extract_download_url(page)

            log.info("Downloading via requests: %s ...", video_url[:100])
            headers = {
                "User-Agent": USER_AGENT,
                "Referer": SAVETIK_URL,
            }

            resp = requests.get(video_url, headers=headers, stream=True, timeout=180)
            resp.raise_for_status()

            total_bytes = 0
            with open(outpath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        total_bytes += len(chunk)

            log.info("✓ Requests download successful!")
            log.info("  File: %s", outpath)
            log.info("  Size: %d bytes", total_bytes)

            browser.close()
            return {
                "success": True,
                "file": outpath,
                "filename": outname,
                "size": total_bytes,
            }

        except Exception as e:
            log.error("Download failed with error: %s", e)
            try:
                browser.close()
            except Exception:
                pass
            return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python savetik.py <douyin_url> [output_dir]")
        sys.exit(1)

    url = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else DOWNLOAD_DIR
    result = download_video(url, output_dir=out)

    if result["success"]:
        print(f"\n✅ Video saved: {result['file']}")
        print(f"   Size: {result['size']} bytes")
    else:
        print(f"\n❌ Error: {result['error']}")
        sys.exit(1)
