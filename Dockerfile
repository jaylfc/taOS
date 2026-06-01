# Stage 1 — Build SPA frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY desktop/package*.json desktop/
RUN cd desktop && npm ci --silent
COPY desktop/ desktop/
RUN cd desktop && npm run build

# Stage 2 — Python backend deps + compile
FROM python:3.12-slim AS backend-builder
WORKDIR /build

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        build-essential \
        git \
        curl \
        libtorrent-rasterbar-dev \
        libboost-python-dev \
        libsqlcipher-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --user --no-cache-dir ".[dev]"

# Stage 3 — Production image
FROM python:3.12-slim

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        libtorrent-rasterbar2.0 \
        libsqlcipher0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=backend-builder /root/.local /root/.local
COPY --from=frontend-builder /build/static/desktop/ static/desktop/
COPY tinyagentos/ tinyagentos/
COPY data/ data/
COPY app-catalog/ app-catalog/
COPY pyproject.toml .

ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

EXPOSE 6969

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:6969/api/health', timeout=5)" || exit 1

CMD ["python3", "-m", "tinyagentos"]
