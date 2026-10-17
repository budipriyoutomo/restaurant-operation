import uuid
from sqlalchemy import Column, Index, String, Table, ForeignKey, TIMESTAMP, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.tenancy import TenantScoped
from app.database import Base

# Many-to-many junction: one PIC can handle multiple categories
pic_categories = Table(
    "pic_categories",
    Base.metadata,
    Column("pic_id", UUID(as_uuid=True), ForeignKey("pics.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class PIC(TenantScoped, Base):
    __tablename__ = "pics"
    __table_args__ = (
        UniqueConstraint("company_id", "email", name="uq_pics_company_email"),
        # One PIC per user account (Todo-Ops-Checklist P2).
        Index("uq_pics_company_user", "company_id", "user_id", unique=True,
              postgresql_where=text("user_id IS NOT NULL AND deleted_at IS NULL")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    email = Column(String(200), nullable=False)
    phone = Column(String(50), nullable=False)
    department = Column(String(100), nullable=False)
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True, default=None)
    # The PIC's user account (P2): routes Issues of the PIC's categories to them
    # when no assignment rule applies. NULL = a contact only (e.g. a vendor).
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    categories = relationship("Category", secondary=pic_categories, lazy="selectin")
    user = relationship("User", lazy="selectin")
