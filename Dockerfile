# ── Use the official Microsoft Playwright image ───────────────────────────────
# This has ALL Chromium system dependencies, fonts, and libraries pre-installed.
# python:3.11-slim was missing ~40 libraries → degraded Chrome fingerprint → CF bot detection.
FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

WORKDIR /app

# Only extra tool we need that isn't in the Playwright base image
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright browsers are already installed in the base image.
# Just make sure the chromium channel is available.
RUN playwright install chromium

COPY . .

RUN mkdir -p /tmp/anime_dl screenshots data

# Increase shared memory for Chromium (default Docker /dev/shm is only 64MB)
# Add --shm-size=2g to your docker run / Koyeb config
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
