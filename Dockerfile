FROM mcr.microsoft.com/playwright/python:v1.46.0-jammy

WORKDIR /app

# ffmpeg for audio processing
# google-chrome-stable for DrissionPage (uses real Chrome, not Chromium)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    wget \
    gnupg \
    && wget -q -O /tmp/chrome.deb \
       https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y /tmp/chrome.deb \
    && rm /tmp/chrome.deb \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Playwright Chromium (for main page scraping)
RUN playwright install chromium

COPY . .

RUN mkdir -p /tmp/anime_dl screenshots data

ENV PYTHONUNBUFFERED=1

# NOTE: Add --shm-size=2g in Koyeb/Docker run config
CMD ["python", "main.py"]
