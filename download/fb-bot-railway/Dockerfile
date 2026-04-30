FROM python:3.11-slim-bookworm

WORKDIR /app

# Install deps first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright Chromium + system deps (this downloads the exact matching browser)
RUN playwright install chromium && playwright install-deps chromium

# Verify Chromium exists
RUN python3 -c "from playwright.sync_api import sync_playwright; pw=sync_playwright().start(); print('Chromium path:', pw.chromium.executable_path); pw.stop()"

# Copy app code
COPY . .

# Persistent data directory
RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python", "main.py"]
