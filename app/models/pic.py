import uuid
from sqlalchemy import Column, String, Table, ForeignKey, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base

# Many-to-many junction: one PIC can handle multiple categories
pic_categories = Table(
    "pic_categories",
    Base.metadata,
    Column("pic_id", UUID(as_uuid=True), ForeignKey("pics.id", ondelete="CASCADE"), primary_key=True),
    Column("category_id", UUID(as_uuid=True), ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
)


class PIC(Base):
    __tablename__ = "pics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    email = Column(String(200), nullable=False, unique=True)
    phone = Column(String(50), nullable=False)
    department = Column(String(100), nullable=False)
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True, default=None)

    categories = relationship("Category", secondary=pic_categories, lazy="selectin")
