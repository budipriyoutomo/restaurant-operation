"""Test configuration.

Uses DATABASE_URL from the environment (same .env as the app).
TEST_DATABASE_URL overrides it if set.

Every test gets a DB session that is rolled back after each test, so the DB
is left clean. Run migrations first before running the test suite.
"""

import os
import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app


# ---------------------------------------------------------------------------
# Auth helpers — used by integration / e2e tests that need real JWT tokens
# ---------------------------------------------------------------------------

def register_and_login(client: TestClient, email: str, password: str, role: str) -> dict:
    """Register a user and return Authorization headers with a valid JWT."""
    client.post("/api/auth/register", json={
        "email": email, "name": f"Test {role.capitalize()}",
        "password": password, "role": role,
    })
    res = client.post("/api/auth/login", json={"email": email, "password": password})
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def seed_user_headers(db, email: str, role: str, name: str = None) -> dict:
    """Insert a user row directly and return Authorization headers with a valid JWT.

    Unlike register_and_login, this needs no pre-existing admin — it seeds the
    user straight into the (rolled-back) test session, so it works on a fresh DB
    even though POST /api/auth/register is admin-guarded.
    """
    from app.models.user import User
    from app.services.auth_service import create_access_token, hash_password

    user = User(
        email=email,
        name=name or f"Test {role.capitalize()}",
        password_hash=hash_password("Pass1234!"),
        role=role,
    )
    db.add(user)
    db.flush()
    token = create_access_token(str(user.id), user.email, role)
    return {"Authorization": f"Bearer {token}"}

# Use TEST_DATABASE_URL if set, otherwise fall back to DATABASE_URL
_DB_URL = os.getenv("TEST_DATABASE_URL") or os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:password@localhost:5432/restaurantops_test",
)

engine = create_engine(_DB_URL)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="session")
def create_tables():
    """Create all tables once per test session (only runs when db fixture is used)."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db(create_tables):
    """Yield a DB session that is rolled back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSession(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db):
    """FastAPI TestClient with the test DB session injected."""
    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
