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
    role = Column(String(50), nullable=False, default="staff")   # staff | manager | admin
    is_active = Column(Boolean, nullable=False, default=True)
    preferences = Column(JSONB, nullable=False, default=dict)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Outlets this user is scoped to. Empty for admins (they see everything) and
    # for users not yet assigned — Tier 4.2 treats "no outlets" as deny-by-default
    # for non-admins, never as "see all".
    outlets = relationship("Outlet", secondary=user_outlets, lazy="selectin")
