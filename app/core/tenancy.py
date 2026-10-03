"""Multi-tenancy — every business row belongs to exactly one company (Todo-Pilot §11).

Enforced by the ORM for every Session, so no query can forget it:

  reads   do_orm_execute adds `company_id = <current company>` to every SELECT,
          UPDATE and DELETE that touches a TenantScoped model (joins, eager and
          lazy loads, Session.get and aggregates included).
  writes  before_flush fills company_id on new rows from the context, refuses
          a row for another company, and refuses any change of company_id.

Fail closed: a statement on a tenant table with no company in context raises
TenantContextError — it never returns every company's rows.

The current company lives on the Session (`session.info`), not in a
contextvar: FastAPI runs sync dependencies and the endpoint in separate
threadpool calls, so a contextvar set while authenticating would not reach the
endpoint, but they share the same Session object.

  tenant(db, company_id)   — run a block as one company (request auth, jobs)
  bypass_tenant(db)        — cross-company system work: login lookup, platform
                             admin, outbox delivery. Use sparingly.

Not covered: raw SQL via text(). The few uses (number sequences) take the
company explicitly — see app/services/numbering.py.

Outlet scoping (outlet_scope_service) still applies inside a company as the
second layer.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional

from sqlalchemy import Column, ForeignKey, event, inspect
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import ORMExecuteState, Session, declared_attr, with_loader_criteria
from sqlalchemy.sql import visitors
from sqlalchemy.sql.base import ExecutableOption
from sqlalchemy.sql.selectable import Select, TableClause

_COMPANY = "tenant_company_id"
_BYPASS = "tenant_bypass"

# Tables that belong to no company.
GLOBAL_TABLES = frozenset({"companies"})
# Pure link tables: both sides are tenant rows, so they are scoped through them.
ASSOCIATION_TABLES = frozenset({"user_outlets", "pic_categories", "role_outlets"})


class TenantContextError(RuntimeError):
    """A tenant table was touched without (or against) the current company."""


class TenantScoped:
    """Mixin for every business model: adds company_id and opts into enforcement."""

    @declared_attr
    def company_id(cls):
        return Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="RESTRICT"),
                      nullable=False, index=True)


def company_key_column() -> Column:
    """company_id as part of the primary key — number-sequence tables are
    counted per (company, year), so every company starts at 00001."""
    return Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

def current_tenant(db: Session) -> Optional[uuid.UUID]:
    return db.info.get(_COMPANY)


def is_bypassed(db: Session) -> bool:
    return bool(db.info.get(_BYPASS))


def set_tenant(db: Session, company_id: Optional[uuid.UUID]) -> None:
    db.info[_COMPANY] = company_id


def require_tenant(db: Session) -> uuid.UUID:
    company_id = current_tenant(db)
    if company_id is None:
        raise TenantContextError("No company in context")
    return company_id


@contextmanager
def tenant(db: Session, company_id: Optional[uuid.UUID]) -> Iterator[None]:
    prev_company, prev_bypass = db.info.get(_COMPANY), db.info.get(_BYPASS)
    db.info[_COMPANY], db.info[_BYPASS] = company_id, False
    try:
        yield
    finally:
        db.info[_COMPANY], db.info[_BYPASS] = prev_company, prev_bypass


@contextmanager
def bypass_tenant(db: Session) -> Iterator[None]:
    prev = db.info.get(_BYPASS)
    db.info[_BYPASS] = True
    try:
        yield
    finally:
        db.info[_BYPASS] = prev


def for_each_company(db: Session, job: Callable[[], List]) -> List:
    """Run `job` once per *active* company, each in that company's context, and
    concatenate the results. For cron jobs (escalation, PM generation), which
    have no user to take a company from. Inactive companies are skipped."""
    from app.models.company import Company
    with bypass_tenant(db):
        company_ids = [c.id for c in db.query(Company).filter(Company.is_active.is_(True)).order_by(Company.created_at)]
    results: List = []
    # Jobs commit; with expire-on-commit a later company's commit would expire
    # the rows already returned, and reading them outside their company fails.
    expire, db.expire_on_commit = db.expire_on_commit, False
    try:
        for company_id in company_ids:
            with tenant(db, company_id):
                results.extend(job() or [])
    finally:
        db.expire_on_commit = expire
    return results


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

def _tenant_table_names(session: Session) -> frozenset:
    names = session.info.get("_tenant_tables")
    if names is None:
        from app.database import Base
        names = frozenset(m.local_table.name for m in Base.registry.mappers if issubclass(m.class_, TenantScoped))
        session.info["_tenant_tables"] = names
    return names


def _touches_tenant_tables(state: ORMExecuteState) -> bool:
    """True if any tenant table appears anywhere in the statement — including
    inside subqueries, where all_mappers does not look (e.g. Query.count())."""
    if any(issubclass(m.class_, TenantScoped) for m in state.all_mappers):
        return True
    names = _tenant_table_names(state.session)
    return any(isinstance(el, TableClause) and el.name in names for el in visitors.iterate(state.statement))


@event.listens_for(Session, "do_orm_execute")
def _filter_by_company(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.session.info.get(_BYPASS) or not _touches_tenant_tables(state):
        return
    company_id = state.session.info.get(_COMPANY)
    if company_id is None:
        raise TenantContextError("Query on company data with no company in context")
    criteria = with_loader_criteria(TenantScoped, lambda cls: cls.company_id == company_id, include_aliases=True)
    state.statement = _with_criteria(state.statement, criteria)


def _with_criteria(statement, criteria):
    """Attach the criteria to the statement *and every SELECT nested in it*.

    An option on the outer statement does not reach subqueries, so
    Query.count(), IN (SELECT …) and select_from(subquery) would otherwise
    read every company's rows (caught by tests/test_tenancy.py)."""
    def nested(element, **kw):
        if isinstance(element, ExecutableOption):
            return element                       # loader options: leave untouched, don't clone
        if element is not statement and isinstance(element, Select):
            return _with_criteria(element, criteria)
        return None
    return visitors.replacement_traverse(statement, {}, nested).options(criteria)


@event.listens_for(Session, "before_flush")
def _stamp_and_guard_company(session: Session, flush_context, instances) -> None:
    if session.info.get(_BYPASS):
        return
    company_id = session.info.get(_COMPANY)
    for obj in session.new:
        if not isinstance(obj, TenantScoped):
            continue
        if company_id is None:
            raise TenantContextError(f"Cannot create {type(obj).__name__} with no company in context")
        if obj.company_id is None:
            obj.company_id = company_id
        elif obj.company_id != company_id:
            raise TenantContextError(f"Cannot create {type(obj).__name__} for another company")
    for obj in session.dirty:
        if isinstance(obj, TenantScoped) and inspect(obj).attrs.company_id.history.deleted:
            raise TenantContextError(f"company_id of {type(obj).__name__} cannot be changed")
