#!/usr/bin/env bash
# Render build script — installs system deps + Playwright Chromium
set -e

echo "=== Installing system dependencies for Chromium ==="
apt-get update -qq
apt-get install -y -qq \
    libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 libdbus-1-3 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 \
    libcups2 libgtk-3-0 libxshmfence1

echo "=== Installing Playwright browsers ==="
PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/.cache/ms-playwright pip install playwright
PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/.cache/ms-playwright python -m playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=/opt/render/project/.cache/ms-playwright python -m playwright install-deps chromium

echo "=== Build complete ==="
