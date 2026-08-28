FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Deno gives yt-dlp an external JS runtime, so YouTube's CPU-heavy, pure-Python
# nsig deciphering runs in a subprocess instead of on the bot's asyncio event
# loop. This fixes the multi-second "Event loop stall detected" bursts that made
# the first /play fail with "interaction expired / application did not respond".
# yt-dlp auto-detects deno on PATH.
RUN curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && rm /tmp/deno.zip \
    && deno --version

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
