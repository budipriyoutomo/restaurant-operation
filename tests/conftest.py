"""Test configuration.

Uses DATABASE_URL from the environment (same .env as the app).
TEST_DATABASE_URL overrides it if set.

Every test gets a DB session that is rolled back after each test, so the DB
is left clean. Run migrations first before running the test suite.
"""

import os
import pytest

from dotenv import load_dotenv
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app


# ---------------------------------------------------------------------------
# Auth helpers — used by integration / e2e tests that need real JWT tokens
# ---------------------------------------------------------------------------

def seed_user_headers(db, email: str, role: str, name: str = None,
                      password: str = "Pass1234!", outlets: "list | None" = None) -> dict:
    """Insert a user row directly and return Authorization headers with a valid JWT.

    POST /api/auth/register is admin-guarded (RBAC) and a fresh test DB has no
    admin to authorize it, so tests seed users straight into the (rolled-back)
    test session instead. Idempotent: re-seeding the same email reuses the row.
    """
    from app.models.user import User
    from app.services.auth_service import create_access_token, hash_password

    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            email=email,
            name=name or f"Test {role.capitalize()}",
            password_hash=hash_password(password),
            role=role,
        )
        db.add(user)
        db.flush()

    # Outlet scoping (Tier 4.2b). Admins bypass scoping, so their assignment is
    # irrelevant. For non-admins the default is "assigned to every outlet the
    # suite seeds", which keeps ordinary tests exercising their own data.
    # Cross-outlet denial tests pass `outlets=[...]` explicitly to pin access.
    if role != "admin":
        from app.models.outlet import Outlet
        if outlets is None:
            names = [n for n, _ in TEST_OUTLETS]
            user.outlets = db.query(Outlet).filter(Outlet.name.in_(names)).all()
        else:
            user.outlets = list(outlets)
        db.flush()

    token = create_access_token(str(user.id), user.email, user.role)
    return {"Authorization": f"Bearer {token}"}


def _session_from_client() -> "Session":
    """Recover the DB session the TestClient is bound to (via the get_db override)."""
    from app.database import get_db
    from app.main import app

    override = app.dependency_overrides.get(get_db)
    if override is None:
        raise RuntimeError(
            "register_and_login requires the `client` fixture (it installs the get_db override)."
        )
    return next(override())


def register_and_login(client: TestClient, email: str, password: str, role: str) -> dict:
    """Create a user and return Authorization headers with a valid JWT.

    Kept for the existing call sites. It seeds the user directly rather than
    calling POST /api/auth/register, which is admin-only — on a fresh test DB
    there is no admin to authorize that call. See seed_user_headers.
    """
    db = _session_from_client()
    return seed_user_headers(db, email, role, password=password)

# ---------------------------------------------------------------------------
# Test database resolution
#
# SAFETY: create_tables() calls Base.metadata.drop_all() on teardown. If the
# suite ever pointed at the application database it would destroy it. So the
# test DB must be given explicitly via TEST_DATABASE_URL, and we hard-fail if
# it resolves to the same URL as the app's DATABASE_URL.
# ---------------------------------------------------------------------------

load_dotenv()   # so TEST_DATABASE_URL / DATABASE_URL in .env are visible here

# Uploads (Tier 5.1) must land in a throwaway dir, never the repo's ./var.
# settings is already instantiated at this point, so mutate the singleton.
import tempfile as _tempfile
from app.config import settings as _settings
_settings.STORAGE_DIR = _tempfile.mkdtemp(prefix="restaurantops-test-uploads-")

_DEFAULT_TEST_URL = "postgresql+psycopg://postgres:@localhost:5432/restaurantops_test"
_APP_URL  = os.getenv("DATABASE_URL")
_TEST_URL = os.getenv("TEST_DATABASE_URL")

_DB_URL = _TEST_URL or _DEFAULT_TEST_URL

if _APP_URL and _DB_URL == _APP_URL:
    raise RuntimeError(
        "Refusing to run tests against the application database "
        f"({_APP_URL!r}) — the suite drops all tables on teardown.\n"
        "Set TEST_DATABASE_URL to a dedicated test database (see .env.example)."
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


# Outlet names used across the test suite. Since Tier 4.1 an outlet name must
# exist in master data (an unknown name is a 422, so a typo can't silently
# become a globally-visible record), so the suite seeds them up front.
TEST_OUTLETS = [
    ("Jakarta", "JKT"),
    ("Bandung", "BDG"),
    ("Dago", "DAGO"),
    ("Outlet Kuala Lumpur", "OKL"),
]


@pytest.fixture(autouse=True)
def seed_outlets(db):
    """Ensure the outlets referenced by tests exist in master data."""
    from app.models.outlet import Outlet

    for name, code in TEST_OUTLETS:
        if db.query(Outlet).filter(Outlet.name == name).first() is None:
            db.add(Outlet(name=name, code=code, status="operational"))
    db.flush()
    return db


@pytest.fixture()
def client(db):
    """FastAPI TestClient with the test DB session injected."""
    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()
