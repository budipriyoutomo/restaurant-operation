from sqlalchemy import Boolean, Column, ForeignKey, String, Table, TIMESTAMP
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


# Default outlets for a role (migration 031). Only consulted when the role is
# not `all_outlets` and the user has no personal override in `user_outlets`.
role_outlets = Table(
    "role_outlets",
    Base.metadata,
    Column("role_key", String(50), ForeignKey("roles.key", ondelete="CASCADE", onupdate="CASCADE"), primary_key=True),
    Column("outlet_id", UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now(), nullable=False),
)


class Role(Base):
    """A configurable role: which modules it may use and which outlets' data it sees.

    `key` is the stable identifier stored in users.role (and the JWT); `name`
    is the display label and may be renamed freely.
    """

    __tablename__ = "roles"

    key = Column(String(50), primary_key=True)
    name = Column(String(100), nullable=False)
    description = Column(String(500), nullable=True)
    # module key -> none | view | manage (see app/permissions.py)
    permissions = Column(JSONB, nullable=False, default=dict)
    # True = every outlet; False = only `outlets` below
    all_outlets = Column(Boolean, nullable=False, default=False)
    # Which approver_role this role acts as in approval workflows: staff | manager | admin
    approval_tier = Column(String(20), nullable=False, default="staff")
    is_system = Column(Boolean, nullable=False, default=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    outlets = relationship("Outlet", secondary=role_outlets, lazy="selectin")
