# Use official Python slim image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Copy application files
COPY requirements.txt .
COPY hanime_telegram_bot.py .
COPY web.py .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose port for Flask
EXPOSE 8080

# Run both web server and bot
CMD ["sh", "-c", "gunicorn -w 4 -b 0.0.0.0:8080 web:app & python hanime_telegram_bot.py"]