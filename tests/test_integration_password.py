"""Integration tests — password management (no more SSH to rotate a password).

Written before the implementation (TDD).

  POST /api/auth/me/password                    change my own password (needs the current one)
  POST /api/auth/users/{id}/reset-password      users:manage resets someone else's password;
                                                generated (shown once) or chosen
"""

import pytest

from app.core.tenancy import bypass_tenant
from app.models.company import Company
from app.models.user import User
from app.services.auth_service import create_access_token, hash_password
from tests.conftest import seed_user_headers

OLD = "Pass1234!"          # seed_user_headers' default password
NEW = "BrandNewPass99"


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def _user_id(db, email) -> str:
    with bypass_tenant(db):
        return str(db.query(User).filter(User.email == email).one().id)


@pytest.fixture()
def admin(client, db):
    return seed_user_headers(db, "admin_pw@test.test", "admin")


@pytest.fixture()
def staff(client, db):
    return seed_user_headers(db, "staff_pw@test.test", "staff")


class TestChangeOwnPassword:
    def test_change_then_login_with_new_password(self, client, staff):
        res = client.post("/api/auth/me/password", headers=staff,
                          json={"current_password": OLD, "new_password": NEW})
        assert res.status_code == 200
        assert _login(client, "staff_pw@test.test", NEW).status_code == 200
        assert _login(client, "staff_pw@test.test", OLD).status_code == 401

    def test_wrong_current_password_is_400_and_nothing_changes(self, client, staff):
        res = client.post("/api/auth/me/password", headers=staff,
                          json={"current_password": "nope", "new_password": NEW})
        assert res.status_code == 400
        assert _login(client, "staff_pw@test.test", OLD).status_code == 200

    def test_short_new_password_is_422(self, client, staff):
        res = client.post("/api/auth/me/password", headers=staff,
                          json={"current_password": OLD, "new_password": "short"})
        assert res.status_code == 422

    def test_same_as_current_is_400(self, client, staff):
        # OLD is below the minimum length, so switch to a long one first
        first = client.post("/api/auth/me/password", headers=staff,
                            json={"current_password": OLD, "new_password": NEW})
        fresh = {"Authorization": f"Bearer {first.json()['access_token']}"}   # old token is revoked
        res = client.post("/api/auth/me/password", headers=fresh,
                          json={"current_password": NEW, "new_password": NEW})
        assert res.status_code == 400

    def test_anonymous_is_401(self, client):
        res = client.post("/api/auth/me/password",
                          json={"current_password": OLD, "new_password": NEW})
        assert res.status_code == 401

    def test_platform_admin_can_change_own_password(self, client, db):
        with bypass_tenant(db):
            op = User(email="ops_pw@platform.test", name="Ops", password_hash=hash_password(OLD),
                      role="staff", company_id=None, is_platform_admin=True)
            db.add(op)
            db.flush()
        headers = {"Authorization": f"Bearer {create_access_token(str(op.id), op.email, op.role)}"}
        res = client.post("/api/auth/me/password", headers=headers,
                          json={"current_password": OLD, "new_password": NEW})
        assert res.status_code == 200
        assert _login(client, "ops_pw@platform.test", NEW).status_code == 200


class TestAdminResetPassword:
    def test_generated_password_is_returned_once_and_works(self, client, db, admin, staff):
        uid = _user_id(db, "staff_pw@test.test")
        res = client.post(f"/api/auth/users/{uid}/reset-password", headers=admin, json={})
        assert res.status_code == 200
        generated = res.json()["password"]
        assert len(generated) >= 12
        assert _login(client, "staff_pw@test.test", generated).status_code == 200
        assert _login(client, "staff_pw@test.test", OLD).status_code == 401

    def test_chosen_password_is_used_and_not_echoed(self, client, db, admin, staff):
        uid = _user_id(db, "staff_pw@test.test")
        res = client.post(f"/api/auth/users/{uid}/reset-password", headers=admin,
                          json={"password": NEW})
        assert res.status_code == 200
        assert res.json()["password"] is None
        assert _login(client, "staff_pw@test.test", NEW).status_code == 200

    def test_short_chosen_password_is_422(self, client, db, admin, staff):
        uid = _user_id(db, "staff_pw@test.test")
        res = client.post(f"/api/auth/users/{uid}/reset-password", headers=admin,
                          json={"password": "short"})
        assert res.status_code == 422

    def test_staff_cannot_reset(self, client, db, admin, staff):
        uid = _user_id(db, "admin_pw@test.test")
        res = client.post(f"/api/auth/users/{uid}/reset-password", headers=staff, json={})
        assert res.status_code == 403

    def test_unknown_user_is_404(self, client, admin):
        res = client.post("/api/auth/users/00000000-0000-0000-0000-000000000000/reset-password",
                          headers=admin, json={})
        assert res.status_code == 404

    def test_own_account_must_use_change_password(self, client, db, admin):
        uid = _user_id(db, "admin_pw@test.test")
        res = client.post(f"/api/auth/users/{uid}/reset-password", headers=admin, json={})
        assert res.status_code == 400

    def test_user_of_another_company_is_404(self, client, db, admin):
        from app.core.tenancy import tenant
        from app.services.role_service import ensure_default_roles
        with bypass_tenant(db):
            other = Company(name="Other Co", slug="other-co-pw")
            db.add(other)
            db.flush()
        with tenant(db, other.id):
            ensure_default_roles(db)
            seed_user_headers(db, "outsider_pw@other.test", "admin")
        uid = _user_id(db, "outsider_pw@other.test")
        res = client.post(f"/api/auth/users/{uid}/reset-password", headers=admin, json={})
        assert res.status_code == 404
        assert _login(client, "outsider_pw@other.test", OLD).status_code == 200
