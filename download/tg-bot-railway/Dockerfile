FROM python:3.12-slim

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot
COPY bot.py .

# Data directory
ENV DATA_DIR=/app/data
RUN mkdir -p /app/data

# Railway port
ENV PORT=8080

EXPOSE 8080

CMD ["python3", "bot.py"]
