"""Multi-tenancy foundation (Todo-Pilot §11). Written before the implementation (TDD).

Every business row belongs to one company. The ORM enforces it automatically —
137 hand-written queries cannot each be trusted to remember a filter:

  - reads of tenant tables are filtered to the session's company
  - a read with no company in context fails closed (never "everything")
  - inserts get company_id from the context; it can never be changed
  - unique business keys (outlet code, document numbers, SKU …) are per company
  - document numbers (ISS-, TSK-, APR-, WO-, PR- …) are counted per company
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.tenancy import (
    ASSOCIATION_TABLES, GLOBAL_TABLES, TenantContextError, TenantScoped,
    bypass_tenant, current_tenant, tenant,
)
from app.database import Base
from app.models.company import Company
from app.models.issue import Issue
from app.models.outlet import Outlet
from app.models.role import Role
from app.models.user import User


@pytest.fixture()
def companies(db):
    """Two extra companies, each with one outlet and the default roles."""
    from app.services.role_service import ensure_default_roles
    with bypass_tenant(db):
        a = Company(name="PT Alpha", slug=f"alpha-{uuid.uuid4().hex[:6]}")
        b = Company(name="PT Beta", slug=f"beta-{uuid.uuid4().hex[:6]}")
        db.add_all([a, b])
        db.flush()
    for c, name in ((a, "Alpha Senayan"), (b, "Beta Dago")):
        with tenant(db, c.id):
            ensure_default_roles(db)
            db.add(Outlet(name=name, code="SNY", status="operational"))   # same code in both: allowed
            db.flush()
    return a, b


def _issue(title, outlet, number):
    return Issue(number=number, title=title, description="", outlet=outlet, category="Maintenance",
                 priority="medium", status="open", assignee="Unassigned")


# ---------------------------------------------------------------------------
# Every table is accounted for
# ---------------------------------------------------------------------------

def test_every_table_is_tenant_scoped_or_declared_global():
    mapped = {m.class_.__tablename__: m.class_ for m in Base.registry.mappers}
    problems = []
    for table in Base.metadata.sorted_tables:
        cls = mapped.get(table.name)
        if table.name in GLOBAL_TABLES or table.name in ASSOCIATION_TABLES:
            continue
        if cls is None or not issubclass(cls, TenantScoped):
            problems.append(table.name)
    assert problems == [], f"tables with no company scoping: {problems}"


def test_global_and_association_tables_really_exist():
    names = {t.name for t in Base.metadata.sorted_tables}
    assert (GLOBAL_TABLES | ASSOCIATION_TABLES) <= names


def test_company_id_is_required_except_for_platform_users():
    for m in Base.registry.mappers:
        cls = m.class_
        if issubclass(cls, TenantScoped):
            nullable = cls.__table__.c.company_id.nullable
            assert nullable == (cls is User), cls.__tablename__


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

class TestReads:
    def test_queries_see_only_the_current_company(self, db, companies):
        a, b = companies
        with tenant(db, a.id):
            assert {o.name for o in db.query(Outlet).all()} == {"Alpha Senayan"}
        with tenant(db, b.id):
            assert {o.name for o in db.scalars(select(Outlet)).all()} == {"Beta Dago"}

    def test_get_by_id_of_another_company_is_none(self, db, companies):
        a, b = companies
        with tenant(db, b.id):
            beta_id = db.query(Outlet).one().id
        db.expunge_all()                                     # no identity-map shortcut
        with tenant(db, a.id):
            assert db.get(Outlet, beta_id) is None
            assert db.query(Outlet).filter(Outlet.id == beta_id).first() is None

    def test_aggregates_and_joins_are_filtered(self, db, companies):
        a, b = companies
        for c, outlet in ((a, "Alpha Senayan"), (b, "Beta Dago")):
            with tenant(db, c.id):
                db.add(_issue("Kompor", outlet, "ISS-T-1"))
                db.flush()
        with tenant(db, a.id):
            assert db.query(func.count(Issue.id)).scalar() == 1
            joined = db.query(Issue).join(Outlet, Outlet.name == Issue.outlet).all()
            assert [i.outlet for i in joined] == ["Alpha Senayan"]

    def test_subqueries_are_filtered_too(self, db, companies):
        # Query.count(), IN (subquery) and select_from(subquery) wrap the
        # entity in a subquery — the filter must reach inside it.
        a, b = companies
        with tenant(db, b.id):
            assert db.query(Outlet).count() == 1
            assert db.scalar(select(func.count()).select_from(select(Outlet).subquery())) == 1
            assert db.query(Outlet).filter(Outlet.id.in_(select(Outlet.id))).count() == 1
            nested = select(Outlet.id).where(Outlet.id.in_(select(Outlet.id).where(Outlet.code == "SNY")))
            assert len(db.execute(nested).all()) == 1
            assert db.query(Outlet).filter(~Outlet.id.in_(select(Outlet.id))).count() == 0

    def test_no_company_in_context_fails_closed(self, db, companies):
        with tenant(db, None):
            with pytest.raises(TenantContextError):
                db.query(Outlet).all()

    def test_global_tables_need_no_context(self, db, companies):
        with tenant(db, None):
            assert db.query(Company).count() >= 2

    def test_bypass_sees_every_company(self, db, companies):
        with bypass_tenant(db):
            names = {o.name for o in db.query(Outlet).all()}
        assert {"Alpha Senayan", "Beta Dago"} <= names

    def test_context_is_restored_after_the_block(self, db, companies):
        a, b = companies
        before = current_tenant(db)
        with tenant(db, a.id):
            with bypass_tenant(db):
                pass
            assert current_tenant(db) == a.id
        assert current_tenant(db) == before


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

class TestWrites:
    def test_insert_takes_the_company_from_context(self, db, companies):
        a, _ = companies
        with tenant(db, a.id):
            o = Outlet(name="Alpha Kemang", code="KMG", status="operational")
            db.add(o)
            db.flush()
            assert o.company_id == a.id

    def test_insert_without_context_is_refused(self, db, companies):
        with tenant(db, None):
            db.add(Outlet(name="Nowhere", code="NWH", status="operational"))
            with pytest.raises(TenantContextError):
                db.flush()
        db.rollback()

    def test_insert_into_another_company_is_refused(self, db, companies):
        a, b = companies
        with tenant(db, a.id):
            db.add(Outlet(name="Sneaky", code="SNK", status="operational", company_id=b.id))
            with pytest.raises(TenantContextError):
                db.flush()
        db.rollback()

    def test_company_id_cannot_be_changed(self, db, companies):
        a, b = companies
        with tenant(db, a.id):
            o = db.query(Outlet).one()
            o.company_id = b.id
            with pytest.raises(TenantContextError):
                db.flush()
        db.rollback()

    def test_bulk_update_and_delete_stay_inside_the_company(self, db, companies):
        a, b = companies
        with tenant(db, a.id):
            db.query(Outlet).update({Outlet.status: "critical"}, synchronize_session=False)
        with tenant(db, b.id):
            assert db.query(Outlet).one().status == "operational"
        with tenant(db, a.id):
            db.query(Outlet).filter(Outlet.code == "SNY").delete(synchronize_session=False)
        with tenant(db, b.id):
            assert db.query(Outlet).count() == 1


# ---------------------------------------------------------------------------
# Unique keys and numbering are per company
# ---------------------------------------------------------------------------

class TestPerCompanyKeys:
    def test_same_outlet_code_in_two_companies_but_not_twice_in_one(self, db, companies):
        a, _ = companies                                  # both already have code SNY
        with tenant(db, a.id):
            db.add(Outlet(name="Alpha Dup", code="SNY", status="operational"))
            with pytest.raises(IntegrityError):
                db.flush()
        db.rollback()

    def test_document_numbers_are_counted_per_company(self, db, companies):
        from app.services.numbering import next_number
        a, b = companies
        with tenant(db, a.id):
            first_a = next_number(db, "ISS", "issue_number_sequences")
            second_a = next_number(db, "ISS", "issue_number_sequences")
        with tenant(db, b.id):
            first_b = next_number(db, "ISS", "issue_number_sequences")
        assert first_a.endswith("-00001") and second_a.endswith("-00002")
        assert first_b == first_a                          # B starts at 1 too

    def test_numbering_needs_a_company(self, db, companies):
        from app.services.numbering import next_number
        with tenant(db, None):
            with pytest.raises(TenantContextError):
                next_number(db, "ISS", "issue_number_sequences")

    def test_each_company_has_its_own_roles(self, db, companies):
        a, b = companies
        with tenant(db, a.id):
            admin_a = db.query(Role).filter(Role.key == "admin").one()
            admin_a.name = "Owner"
            db.flush()
        with tenant(db, b.id):
            assert db.query(Role).filter(Role.key == "admin").one().name != "Owner"

    def test_a_user_resolves_its_own_companys_role(self, db, companies):
        from app.services.auth_service import hash_password
        a, b = companies
        with tenant(db, b.id):
            db.query(Role).filter(Role.key == "manager").one().name = "Beta Boss"
            db.flush()
        with tenant(db, a.id):
            u = User(email=f"m-{uuid.uuid4().hex[:6]}@alpha.test", name="M", password_hash=hash_password("x"),
                     role="manager")
            db.add(u)
            db.flush()
            db.expire(u)
            assert u.role_obj.company_id == a.id and u.role_obj.name != "Beta Boss"
