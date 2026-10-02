"""Columns that are native Postgres enums in the migrations must be declared as
Enum in the models too.

Declared as String, psycopg sends ::VARCHAR and Postgres rejects the INSERT
("column status is of type training_program_status but expression is of type
character varying"). The test DB is built from the models (create_all), so the
API tests cannot see this — creating a training program or a campaign was a 500
on every migrated database. scripts/check_schema_drift.py does the full
comparison against a migrated DB.
"""

import pytest
from sqlalchemy import Enum

import app.main  # noqa: F401 — registers every model
from app.database import Base

# (table, column) → (Postgres enum name, values) as created by the migrations
MIGRATION_ENUMS = {
    ("training_programs", "status"): ("training_program_status", ["scheduled", "ongoing", "completed", "cancelled"]),
    ("campaigns", "type"): ("campaign_type", ["promotion", "event", "social-media", "email", "other"]),
    ("campaigns", "status"): ("campaign_status", ["draft", "active", "completed", "cancelled"]),
}


@pytest.mark.parametrize("table,column", list(MIGRATION_ENUMS))
def test_model_declares_the_native_enum(table, column):
    col_type = Base.metadata.tables[table].c[column].type
    name, values = MIGRATION_ENUMS[(table, column)]
    assert isinstance(col_type, Enum), f"{table}.{column} must be Enum({name})"
    assert col_type.name == name
    assert list(col_type.enums) == values
