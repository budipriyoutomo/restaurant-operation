"""Assignee routing rules — category × outlet → user (Todo-Ops-Checklist P2, migration 045)."""

import uuid

from sqlalchemy import Column, ForeignKey, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped
from app.database import Base


class IssueAssignmentRule(TenantScoped, Base):
    """A new Issue of `category` at `outlet` (NULL = every outlet) with no
    assignee goes to `assignee_user`. See app/services/assignment_rules.py."""

    __tablename__ = "issue_assignment_rules"
    __table_args__ = (
        UniqueConstraint("company_id", "category_id", "outlet_id", name="uq_issue_assignment_rules_scope",
                         postgresql_nulls_not_distinct=True),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True)
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="CASCADE"), nullable=True)
    assignee_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    category = relationship("Category", lazy="selectin")
    outlet = relationship("Outlet", lazy="selectin")
    assignee = relationship("User", lazy="selectin")
