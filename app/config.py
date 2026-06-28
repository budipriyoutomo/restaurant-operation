from pydantic_settings import BaseSettings
from typing import List
import secrets


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg://postgres:password@localhost:5432/restaurantops"
    CORS_ORIGINS: str = "http://localhost:3000"

    # Security — generate a strong key: python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str = secrets.token_hex(32)
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # Application
    ENVIRONMENT: str = "development"   # development | staging | production

    # Rate limiting (requests per minute per IP)
    RATE_LIMIT_DEFAULT: str = "60/minute"
    RATE_LIMIT_WRITE: str = "20/minute"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",")]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    model_config = {"env_file": ".env"}


settings = Settings()
