# Use the latest official Python slim image (Python 3.13 as of October 2025)
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    xz-utils \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libcairo2 \
    libcups2 \
    libdrm2 \
    libxfixes3 \
    && rm -rf /var/lib/apt/lists/*

# Copy application files
COPY requirements.txt .
COPY hanime_telegram_bot.py .
COPY web.py .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Ensure Playwright cache directory is writable
RUN mkdir -p /root/.cache/ms-playwright && chmod -R 777 /root/.cache/ms-playwright

# Install Playwright browsers as root
USER root
RUN playwright install chromium --with-deps && playwright install-deps

# Expose port for Flask
EXPOSE 8080

# Run both web server and bot
CMD ["sh", "-c", "gunicorn -w 4 -b 0.0.0.0:8080 web:app & python hanime_telegram_bot.py"]