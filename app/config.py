from pydantic import model_validator
from pydantic_settings import BaseSettings
from typing import List
import secrets
import warnings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://postgres:password@localhost:5432/restaurantops"
    CORS_ORIGINS: str = "http://localhost:3000"

    # Security — generate a strong key: python -c "import secrets; print(secrets.token_hex(32))"
    # Must be set explicitly. A per-process random key would invalidate every JWT
    # on restart, and under multiple workers each worker would sign with a
    # different key — tokens issued by one worker would 401 on another.
    SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # Application
    ENVIRONMENT: str = "development"   # development | staging | production

    # Rate limiting (requests per minute per IP)
    RATE_LIMIT_DEFAULT: str = "60/minute"
    RATE_LIMIT_WRITE: str = "20/minute"

    # File storage (Tier 5.1 — work-order photos).
    # STORAGE_BACKEND=local writes under STORAGE_DIR; the abstraction leaves room
    # for an s3 backend later without touching call sites.
    STORAGE_BACKEND: str = "local"          # local | s3 (s3 not implemented yet)
    STORAGE_DIR: str = "./var/uploads"
    MAX_UPLOAD_MB: int = 10

    # Corrective work orders whose estimated cost (IDR) is above this need
    # approval. Per-outlet override: outlets.approval_threshold (migration 033).
    APPROVAL_THRESHOLD_DEFAULT: int = 1_000_000

    # Email notifications (Todo-Next §4). Off unless SMTP_HOST is set.
    # EMAIL_BACKEND: smtp | console (log only) | memory (tests) | disabled.
    # Left empty, it resolves to smtp when SMTP_HOST is set, else disabled.
    EMAIL_BACKEND: str = ""
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_STARTTLS: bool = True
    SMTP_FROM: str = "RestaurantOps <no-reply@localhost>"
    SMTP_TIMEOUT: int = 15
    # Public frontend URL, used for links in emails. Empty = no link.
    APP_URL: str = ""

    # Guest Service KPI (Todo-Pilot §8): a complaint should get a first response
    # within this many minutes of being reported.
    GUEST_FIRST_RESPONSE_TARGET_MINUTES: int = 60

    # WhatsApp notifications via WuzAPI (Todo-Pilot §4). Off unless WUZAPI_URL is set.
    # WHATSAPP_BACKEND: wuzapi | console (log only) | memory (tests) | disabled.
    # Left empty, it resolves to wuzapi when WUZAPI_URL is set, else disabled.
    WHATSAPP_BACKEND: str = ""
    WUZAPI_URL: str = ""                 # e.g. http://wuzapi:8080
    WUZAPI_TOKEN: str = ""               # the WuzAPI *user* token (sent as the Token header)
    WUZAPI_TIMEOUT: int = 10
    WHATSAPP_MAX_PER_HOUR: int = 20      # per recipient; extra messages are skipped, not queued
    WHATSAPP_DEDUP_MINUTES: int = 10     # same event + record + recipient inside this window = one message
    WHATSAPP_MAX_ATTEMPTS: int = 4       # then the message is marked failed
    # Images only — technicians photograph work; documents go via the URL path.
    ALLOWED_UPLOAD_MIME: str = "image/jpeg,image/png,image/webp"

    @property
    def allowed_upload_mime_list(self) -> List[str]:
        return [m.strip() for m in self.ALLOWED_UPLOAD_MIME.split(",") if m.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    @property
    def email_backend(self) -> str:
        if self.EMAIL_BACKEND:
            return self.EMAIL_BACKEND
        return "smtp" if self.SMTP_HOST else "disabled"

    @property
    def whatsapp_backend(self) -> str:
        if self.WHATSAPP_BACKEND:
            return self.WHATSAPP_BACKEND
        return "wuzapi" if self.WUZAPI_URL else "disabled"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @model_validator(mode="after")
    def _require_secret_key(self):
        if not self.SECRET_KEY:
            if self.is_production:
                raise ValueError(
                    "SECRET_KEY must be set when ENVIRONMENT=production. "
                    'Generate one: python -c "import secrets; print(secrets.token_hex(32))"'
                )
            # Dev fallback: usable, but sessions won't survive a restart.
            self.SECRET_KEY = secrets.token_hex(32)
            warnings.warn(
                "SECRET_KEY is not set — using a random per-process key. "
                "Every restart will log all users out. Set SECRET_KEY in .env.",
                RuntimeWarning,
                stacklevel=2,
            )
        return self

    # extra="ignore": .env also carries non-application settings (e.g.
    # TEST_DATABASE_URL, read by the test suite), which must not fail app startup.
    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
