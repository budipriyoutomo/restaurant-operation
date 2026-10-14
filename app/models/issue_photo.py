"""Photos attached to an Issue when it is reported (Todo-Pilot §20, migration 043)."""

import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped
from app.database import Base


class IssuePhoto(TenantScoped, Base):
    __tablename__ = "issue_photos"

    id            = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id      = Column(UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True)
    caption       = Column(String(500), nullable=True)
    uploaded_by   = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    storage_key   = Column(String(300), nullable=False)
    thumbnail_key = Column(String(300), nullable=True)
    mime_type     = Column(String(100), nullable=True)
    size_bytes    = Column(Integer, nullable=True)
    created_at    = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    issue = relationship("Issue", back_populates="photos")
