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
    # Images only — technicians photograph work; documents go via the URL path.
    ALLOWED_UPLOAD_MIME: str = "image/jpeg,image/png,image/webp"

    @property
    def allowed_upload_mime_list(self) -> List[str]:
        return [m.strip() for m in self.ALLOWED_UPLOAD_MIME.split(",") if m.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

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
