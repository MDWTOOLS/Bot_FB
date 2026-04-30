FROM python:3.11-slim-bookworm

WORKDIR /app

# Install Playwright deps + Chromium
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium \
    && playwright install-deps chromium

COPY . .

# Persistent data directory (Railway Volume mount here)
RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python", "main.py"]
