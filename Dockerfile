# Use Python 3.11 slim image (small size, fast build)
FROM python:3.11-slim-bookworm

WORKDIR /app

# ── Install Chromium system dependencies ────────────────────────────────
# Render Docker mein apt-get allowed hai — yahi fix hai!
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libcups2 \
    libgtk-3-0 \
    libxshmfence1 \
    libu2f-udev \
    libvulkan1 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# ── Install Python dependencies ─────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Install Playwright Chromium ─────────────────────────────────────────
ENV PLAYWRIGHT_BROWSERS_PATH=/app/.cache/ms-playwright
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium

# ── Copy application code ───────────────────────────────────────────────
COPY . .

# ── Create download directory ───────────────────────────────────────────
RUN mkdir -p /tmp/savetik_downloads

# ── Expose port and run ─────────────────────────────────────────────────
ENV PORT=10000
EXPOSE 10000

CMD gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 180
