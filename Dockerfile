# Unified dashboard service (detector + agent co-located, design.md §2)
FROM python:3.12-slim

WORKDIR /app

# System libs for OpenCV video decode (headless still needs libGL-free deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 && rm -rf /var/lib/apt/lists/*

COPY packages/shared packages/shared
RUN pip install --no-cache-dir -e packages/shared

# Explicit union of service deps (avoids -e relative paths in requirements)
# google-adk pulls a compatible fastapi/starlette/uvicorn; pin to known-good.
RUN pip install --no-cache-dir \
    google-adk==2.3.0 asyncpg==0.30.0 "sqlalchemy[asyncio]==2.0.36" pgvector==0.3.6 \
    "fastapi==0.139.0" sse-starlette==2.1.3 "uvicorn[standard]==0.34.0" jinja2==3.1.5 \
    google-cloud-storage==2.19.0 \
    opencv-python-headless==5.0.0.93 numpy==2.5.0

COPY services/detector services/detector
COPY services/agent services/agent
COPY services/dashboard services/dashboard
COPY video video

ENV PYTHONPATH=/app/services/dashboard:/app/services/detector:/app/services/agent \
    PYTHONUNBUFFERED=1 \
    DET_SAMPLE_FPS=5

# Cloud Run provides $PORT (default 8080)
CMD exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080} --app-dir services/dashboard
