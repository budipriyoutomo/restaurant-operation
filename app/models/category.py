import uuid
from sqlalchemy import Column, String, Text, Enum as SAEnum, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base
from app.models.enums import CategoryTypeEnum


def _sa_enum(py_enum, pg_name):
    return SAEnum(
        py_enum,
        values_callable=lambda x: [e.value for e in x],
        name=pg_name,
        create_type=False,
    )


class Category(Base):
    __tablename__ = "categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    type = Column(_sa_enum(CategoryTypeEnum, "category_type"), nullable=False, default=CategoryTypeEnum.operations)
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True, default=None)
