# RestaurantOps — Issue Core API
# Production image: FastAPI under Gunicorn + Uvicorn workers, Python 3.14.

FROM python:3.14-slim AS base

# - PYTHONDONTWRITEBYTECODE: no .pyc clutter in the image
# - PYTHONUNBUFFERED: logs stream straight to Docker
# - PIP_NO_CACHE_DIR: smaller layer
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# psycopg[binary] and Pillow ship manylinux wheels, so no compiler/libpq needed.
# curl is only for the container HEALTHCHECK.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Application code.
COPY . .
RUN chmod +x /app/docker/entrypoint.sh

# Drop privileges. The uploads volume is chowned to this user in the compose file.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/uploads \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app.main:app"]
