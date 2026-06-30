#!/usr/bin/env python3
"""
Savetik.co Douyin Video Downloader
===================================
Professional Playwright-based automation to download no-watermark
Douyin (Chinese TikTok) videos via Savetik.co.

Usage:
    python savetik_downloader.py <douyin_url> [output_filename]

Requirements:
    pip install playwright requests
    playwright install chromium
"""

import os
import sys
import re
import time
import logging
from urllib.parse import urlparse, unquote

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SAVETIK_URL = "https://savetik.co/en2"
DEFAULT_TIMEOUT = 60_000        # 60 seconds in milliseconds
NAVIGATION_TIMEOUT = 90_000     # 90 seconds for page load
DOWNLOAD_TIMEOUT = 120_000      # 120 seconds for download completion
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("savetik")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sanitise_filename(url: str) -> str:
    """Derive a sensible filename from a Douyin URL or download link."""
    name = os.path.basename(urlparse(url).path)
    if not name or name.strip() in ("", "/", "video"):
        name = re.sub(r"[^a-zA-Z0-9]", "_", url.split("?")[0])[-40:]
    name = re.sub(r"[^\w\-\.\(\) ]", "_", name)
    if not name.endswith(".mp4"):
        name += ".mp4"
    return name


def extract_download_url(page, timeout: int = 30_000) -> str:
    """
    After the download button has been clicked and Savetik has processed
    the URL, wait for the final download anchor (<a>) or a video source
    element to appear, then extract the direct MP4 link.
    """
    log.info("Waiting for the download link to appear …")

    # Strategy 1: Look for an <a> tag with a download-related class / attribute
    try:
        download_link = page.wait_for_selector(
            "a[download], a.download-btn, a.download-button, "
            "a[href*='.mp4'], a[href*='/download'], "
            "a.btn-success, a[class*='download']",
            timeout=timeout,
        )
        href = download_link.get_attribute("href")
        if href:
            log.info("Found download link in <a> element: %s", href[:80])
            return href
    except PlaywrightTimeout:
        log.debug("No download <a> element found (strategy 1).")

    # Strategy 2: Look for a <video> tag with a <source> child
    try:
        video = page.wait_for_selector("video source[src]", timeout=10_000)
        src = video.get_attribute("src")
        if src:
            log.info("Found download URL in <video> <source>: %s", src[:80])
            return src
    except PlaywrightTimeout:
        log.debug("No <video><source> found (strategy 2).")

    # Strategy 3: Look for any <video> with a src attribute directly
    try:
        video = page.wait_for_selector("video[src]", timeout=10_000)
        src = video.get_attribute("src")
        if src:
            log.info("Found download URL in <video[src]>: %s", src[:80])
            return src
    except PlaywrightTimeout:
        log.debug("No <video[src]> found (strategy 3).")

    # Strategy 4: Grab any visible link containing .mp4 anywhere on the page
    try:
        mp4_links = page.query_selector_all("a[href*='.mp4']")
        for link in mp4_links:
            href = link.get_attribute("href")
            if href:
                log.info("Found .mp4 link in page: %s", href[:80])
                return href
    except Exception:
        pass

    # Strategy 5: Intercept via page content (last resort)
    try:
        page.wait_for_timeout(5000)
        content = page.content()
        # Common patterns for direct video URLs
        patterns = [
            r'(https?://[^\s"\']+\.mp4[^\s"\']*)',
            r'(https?://[^\s"\']+/video/[^\s"\']+)',
            r'(https?://[^\s"\']*\/download[^\s"\']+)',
        ]
        for pat in patterns:
            match = re.search(pat, content)
            if match:
                url_candidate = match.group(1).rstrip("'\"")
                log.info("Extracted candidate URL from page source: %s", url_candidate[:80])
                return url_candidate
    except Exception:
        pass

    raise RuntimeError(
        "Could not extract a download URL from the Savetik page. "
        "The site structure may have changed. Check manually."
    )


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def download_douyin_video(douyin_url: str, output_path: str = None) -> str:
    """
    1. Launch headless Chromium via Playwright.
    2. Navigate to Savetik.co.
    3. Wait for the dynamic form to render.
    4. Paste the Douyin URL into the input field.
    5. Click the download / submit button.
    6. Wait for processing and extract the direct video link.
    7. Stream the video to a local file using requests.
    8. Return the local file path.
    """
    if not output_path:
        output_path = sanitise_filename(douyin_url)

    with sync_playwright() as pw:
        # ---- Launch browser (headless by default) ----
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )

        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            # Accept downloads so Playwright can capture them
            accept_downloads=True,
        )

        # Remove webdriver痕迹 to avoid Cloudflare detection
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)

        # ---- Step 1: Navigate to Savetik ----
        log.info("Navigating to %s …", SAVETIK_URL)
        page.goto(SAVETIK_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT)

        # Bypass Cloudflare / loading screen
        log.info("Waiting for page to settle (bypassing Cloudflare & JS rendering) …")
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except PlaywrightTimeout:
            log.warning("Network did not fully idle; continuing with current state.")

        page.wait_for_timeout(3000)  # Extra buffer for dynamic widgets

        # ---- Step 2: Locate the input field ----
        log.info("Locating URL input field …")
        # Common selectors on TikTok/ Douyin downloader sites
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

        input_element = None
        for sel in input_selectors:
            try:
                input_element = page.wait_for_selector(sel, timeout=8_000)
                if input_element and input_element.is_visible():
                    log.info("Found input field with selector: %s", sel)
                    break
            except PlaywrightTimeout:
                continue

        if not input_element:
            # Fallback: grab any visible text input
            inputs = page.query_selector_all("input[type='text'], input:not([type])")
            for inp in inputs:
                if inp.is_visible():
                    input_element = inp
                    log.info("Found fallback input element.")
                    break

        if not input_element:
            # Dump page state for debugging
            log.error("Page URL: %s", page.url)
            log.error("Page title: %s", page.title())
            raise RuntimeError(
                "Could not locate the URL input field on Savetik.co. "
                "The page may have changed or a CAPTCHA is blocking access."
            )

        # ---- Step 3: Paste the Douyin URL ----
        log.info("Pasting Douyin URL …")
        input_element.click()
        input_element.fill("")
        page.wait_for_timeout(500)
        input_element.fill(douyin_url)
        page.wait_for_timeout(1000)

        # ---- Step 4: Click the download/submit button ----
        log.info("Locating and clicking the download button …")
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

        button = None
        for sel in button_selectors:
            try:
                button = page.wait_for_selector(sel, timeout=5_000)
                if button and button.is_visible():
                    log.info("Found download button with selector: %s", sel)
                    break
            except PlaywrightTimeout:
                continue

        if not button:
            # Try by roles
            button = page.get_by_role("button", name="Download").first
            if not button:
                button = page.get_by_role("button").first

        if not button:
            raise RuntimeError("Could not locate the download button.")

        # ---- Step 5: Initiate download & wait for processing ----
        log.info("Clicking download button …")
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as download_info:
            button.click()
            # Savetik shows a spinner/loading state while processing
            log.info("Waiting for Savetik to process the video …")

        # ---- Step 6: Capture the download ----
        download = download_info.value
        suggested_name = download.suggested_filename or sanitise_filename(douyin_url)
        log.info("Download triggered: '%s'", suggested_name)

        # Save to disk
        if os.path.isdir(output_path):
            save_path = os.path.join(output_path, suggested_name)
        else:
            save_path = output_path if output_path.endswith(".mp4") else output_path

        download.save_as(save_path)
        log.info("Video saved to: %s", save_path)

        # ---- Cleanup ----
        browser.close()
        return save_path


def download_douyin_video_via_requests(
    douyin_url: str, output_path: str = None
) -> str:
    """
    Alternative approach when Playwright's download event is unreliable:
    1. Use Playwright to get the page to reveal the download link.
    2. Extract the direct video URL manually.
    3. Stream the video using `requests`.
    """
    if not output_path:
        output_path = sanitise_filename(douyin_url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT)

        # Navigate
        log.info("Navigating to %s …", SAVETIK_URL)
        page.goto(SAVETIK_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT)
        try:
            page.wait_for_load_state("networkidle", timeout=30_000)
        except PlaywrightTimeout:
            pass
        page.wait_for_timeout(3000)

        # Fill input
        log.info("Filling URL input …")
        input_selectors = [
            "input[type='url']",
            "input[placeholder*='link' i]",
            "input[placeholder*='URL' i]",
            "input[placeholder*='Paste' i]",
            "input[name='url']",
            "input[name='link']",
            "textarea[placeholder*='link' i]",
            "textarea[placeholder*='URL' i]",
        ]
        input_el = None
        for sel in input_selectors:
            try:
                input_el = page.wait_for_selector(sel, timeout=8_000)
                if input_el and input_el.is_visible():
                    break
            except PlaywrightTimeout:
                continue

        if not input_el:
            inputs = page.query_selector_all("input[type='text'], input:not([type])")
            for inp in inputs:
                if inp.is_visible():
                    input_el = inp
                    break

        if not input_el:
            browser.close()
            raise RuntimeError("Could not locate input field.")

        input_el.click()
        input_el.fill("")
        page.wait_for_timeout(500)
        input_el.fill(douyin_url)
        page.wait_for_timeout(1000)

        # Click download button
        log.info("Clicking download button …")
        button_selectors = [
            "button[type='submit']",
            "button:has-text('Download')",
            "button:has-text('Search')",
            "button:has-text('Start')",
            "input[type='submit']",
            "[class*='btn']:has-text('Download')",
            "[class*='btn']:has-text('Search')",
        ]
        btn = None
        for sel in button_selectors:
            try:
                btn = page.wait_for_selector(sel, timeout=5_000)
                if btn and btn.is_visible():
                    break
            except PlaywrightTimeout:
                continue
        if not btn:
            btn = page.get_by_role("button").first

        if not btn:
            browser.close()
            raise RuntimeError("Could not locate download button.")

        btn.click()

        # ---- Extract the direct video URL ----
        video_url = extract_download_url(page, timeout=DOWNLOAD_TIMEOUT)
        browser.close()

    # ---- Download via requests ----
    log.info("Downloading video directly via requests: %s", video_url[:100])
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": SAVETIK_URL,
    }

    resp = requests.get(video_url, headers=headers, stream=True, timeout=120)
    resp.raise_for_status()

    if os.path.isdir(output_path):
        # Try to derive filename from Content-Disposition
        cd = resp.headers.get("Content-Disposition", "")
        fname_match = re.search(r'filename=([^;]+)', cd)
        if fname_match:
            fname = unquote(fname_match.group(1)).strip('" ')
        else:
            fname = sanitise_filename(douyin_url)
        output_path = os.path.join(output_path, fname)

    total_bytes = 0
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                total_bytes += len(chunk)

    log.info("Downloaded %s bytes → %s", total_bytes, output_path)
    return output_path


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    douyin_url = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"{'='*60}")
    print(f" Savetik.co Douyin Downloader")
    print(f" URL: {douyin_url}")
    print(f"{'='*60}")

    try:
        # Try the Playwright-native download first (handles Cloudflare better)
        saved = download_douyin_video(douyin_url, output_path)
        print(f"\n✓ Video saved to: {saved}")
    except Exception as e:
        log.warning("Playwright-native download failed: %s", e)
        log.info("Falling back to requests-based download …")
        try:
            saved = download_douyin_video_via_requests(douyin_url, output_path)
            print(f"\n✓ Video saved to: {saved}")
        except Exception as e2:
            log.error("All download methods failed.")
            log.error("Error: %s", e2)
            sys.exit(1)


if __name__ == "__main__":
    main()
