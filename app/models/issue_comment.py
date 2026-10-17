"""Comments on an Issue (Todo-Ops-Checklist P3, migration 046).

kind = user    written by someone in the drawer
       system  written by a service, e.g. a daily checklist item that failed
               again on an Issue that is still open (keputusan #8)
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, ForeignKey, String, Text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped
from app.database import Base

COMMENT_KINDS = ("user", "system")


class IssueComment(TenantScoped, Base):
    __tablename__ = "issue_comments"
    __table_args__ = (CheckConstraint("kind IN ('user', 'system')", name="issue_comments_kind_check"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    issue_id = Column(UUID(as_uuid=True), ForeignKey("issues.id", ondelete="CASCADE"), nullable=False, index=True)
    author_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_name = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    kind = Column(String(10), nullable=False, default="user", server_default="user")
    # Set by the app, not now(): now() is the transaction start, so comments
    # written in one transaction (a checklist submit) would tie and lose order.
    created_at = Column(TIMESTAMP(timezone=True), default=lambda: datetime.now(timezone.utc),
                        server_default=func.now(), nullable=False)
