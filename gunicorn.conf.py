"""Gunicorn configuration for production.

Overridable via environment variables so the same image works on a small VPS
or a bigger box without a rebuild.
"""

import multiprocessing
import os

# --- Networking -------------------------------------------------------------
bind = os.getenv("BIND", "0.0.0.0:8000")

# Trust X-Forwarded-* from the reverse proxy (Caddy/Nginx/Traefik) so slowapi
# rate-limits on the real client IP and FastAPI builds correct URLs.
forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "*")

# --- Workers ---------------------------------------------------------------
# ASGI worker. SECRET_KEY must be set (config.py enforces it in production) so
# every worker signs JWTs with the same key.
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("WEB_CONCURRENCY", (multiprocessing.cpu_count() * 2) + 1))

# --- Timeouts / lifecycle ------------------------------------------------------
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))

# Recycle workers periodically to bound any slow memory growth.
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "100"))

# --- Logging -------------------------------------------------------------------
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")
