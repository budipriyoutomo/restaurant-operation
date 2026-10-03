"""Document numbers — ISS-2026-00001, TSK-, APR-, WO-, AST-, PR-, PO-, GRN-, AUD- (Todo-Pilot §11).

Counted per (company, year): every company starts at 00001, so one tenant's
volume never shows in another's numbers. The single place that writes the
*_number_sequences tables — raw SQL is not covered by the ORM tenant filter,
so the company is taken from the context explicitly and required.

INSERT … ON CONFLICT … DO UPDATE inside the caller's transaction keeps
concurrent creates from ever getting the same number.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.tenancy import require_tenant

_YEARLY = frozenset({
    "issue_number_sequences", "task_number_sequences", "approval_number_sequences",
    "work_order_number_sequences", "asset_number_sequences", "qa_audit_number_sequences",
})
_PER_PREFIX = "procurement_number_sequences"      # PR / PO / GRN share one table


def next_number(db: Session, prefix: str, table: str) -> str:
    if table not in _YEARLY and table != _PER_PREFIX:
        raise ValueError(f"Unknown sequence table {table!r}")
    company_id = require_tenant(db)
    year = datetime.now().year
    if table == _PER_PREFIX:
        sql = f"""
            INSERT INTO {table} (company_id, prefix, year, last_seq) VALUES (:c, :p, :y, 1)
            ON CONFLICT (company_id, prefix, year) DO UPDATE SET last_seq = {table}.last_seq + 1
            RETURNING last_seq"""
    else:
        sql = f"""
            INSERT INTO {table} (company_id, year, last_seq) VALUES (:c, :y, 1)
            ON CONFLICT (company_id, year) DO UPDATE SET last_seq = {table}.last_seq + 1
            RETURNING last_seq"""
    seq = db.execute(text(sql), {"c": company_id, "p": prefix, "y": year}).scalar_one()
    return f"{prefix}-{year}-{seq:05d}"
