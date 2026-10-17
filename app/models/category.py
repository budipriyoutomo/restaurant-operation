import uuid
from sqlalchemy import Boolean, CheckConstraint, Column, Index, String, Text, TIMESTAMP, func, text
from sqlalchemy.dialects.postgresql import UUID

from app.core.category_defaults import CATEGORY_MODULES
from app.core.tenancy import TenantScoped
from app.database import Base

_MODULES_SQL = ", ".join(f"'{m}'" for m in CATEGORY_MODULES)


class Category(TenantScoped, Base):
    """Issue category — master data per company (Todo-Ops-Checklist P1, migration 044).

    Behaviour comes from `module`; `system_key` marks the built-in rows (see
    app/core/category_defaults.py). Also used by PICs (pic_categories).
    """

    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(f"module IN ({_MODULES_SQL})", name="categories_module_check"),
        CheckConstraint(
            "approval_type IS NULL OR approval_type IN ('procurement', 'marketing', 'training', 'asset-purchase', 'maintenance')",
            name="categories_approval_type_check",
        ),
        # Issues pick a category by name, so active names are unique per company.
        Index("uq_categories_company_name", "company_id", func.lower(text("name")), unique=True,
              postgresql_where=text("deleted_at IS NULL")),
        Index("uq_categories_company_system_key", "company_id", "system_key", unique=True,
              postgresql_where=text("system_key IS NOT NULL")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    system_key = Column(String(40), nullable=True)
    module = Column(String(30), nullable=False, default="general", server_default="general")
    default_generate_task = Column(Boolean, nullable=False, default=True, server_default="true")
    default_generate_approval = Column(Boolean, nullable=False, default=False, server_default="false")
    approval_type = Column(String(30), nullable=True)
    icon = Column(String(40), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True, default=None)
