# syntax=docker/dockerfile:1.7
FROM --platform=$BUILDPLATFORM node:22.14.0-alpine3.21 AS web
WORKDIR /web
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12.10-slim-bookworm AS python-build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY backend/requirements.lock /tmp/requirements.lock
RUN /venv/bin/pip install --only-binary=:all: --require-hashes -r /tmp/requirements.lock

FROM python:3.12.10-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/venv/bin:$PATH STATIC_DIR=/app/static NUMBA_CACHE_DIR=/tmp/numba
RUN useradd --system --uid 10001 --create-home app
WORKDIR /app
COPY --from=python-build /venv /venv
COPY backend/ backend/
COPY geo/ geo/
COPY data/ data/
COPY alembic.ini ./
COPY --from=web /web/dist static/
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]
CMD ["python", "-m", "backend.boot"]

