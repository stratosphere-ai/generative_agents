# PredInsure — prediction-market-backed insurance platform.
# Zero-local-setup image: build once, run anywhere (docker / Render / Railway / Fly).
FROM python:3.12-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY app ./app
COPY static ./static
RUN mkdir -p data

# Offline-safe defaults (no keys needed); override at run time to enable real paths.
ENV INSURE_PROVIDER=polymarket \
    INSURE_DISCOVERY=rule \
    INSURE_MATCH=off \
    PORT=8100

EXPOSE 8100

# Honor the platform-provided $PORT (Render/Railway/Fly set it); default 8100 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8100}"]
