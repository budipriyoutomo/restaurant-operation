import uuid
from sqlalchemy import Column, ForeignKey, String, Boolean, TIMESTAMP, Table
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


# Which outlets a user may see (Tier 4.1). Many-to-many: area managers cover
# several outlets, so a single users.outlet_id would force duplicate accounts.
user_outlets = Table(
    "user_outlets",
    Base.metadata,
    Column("user_id", UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("outlet_id", UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="CASCADE"), primary_key=True),
    Column("created_at", TIMESTAMP(timezone=True), server_default=func.now(), nullable=False),
)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(200), nullable=False, unique=True)
    name = Column(String(200), nullable=False)
    password_hash = Column(String(255), nullable=False)
    # Key of a row in `roles` (migration 031) — staff/manager/admin or a custom role.
    role = Column(String(50), ForeignKey("roles.key", onupdate="CASCADE"), nullable=False, default="staff")
    is_active = Column(Boolean, nullable=False, default=True)
    preferences = Column(JSONB, nullable=False, default=dict)
    # Digits with country code, e.g. 6281234567890 (Todo-Pilot §4). NULL = none.
    whatsapp_number = Column(String(20), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Personal outlet override. Non-empty = exactly these outlets, regardless of
    # the role's default. Empty = inherit the role's outlet access (see
    # outlet_scope_service.allowed_outlet_ids).
    outlets = relationship("Outlet", secondary=user_outlets, lazy="selectin")
    role_obj = relationship("Role", lazy="joined")
