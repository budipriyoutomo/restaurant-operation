import uuid
from sqlalchemy import Column, String, Enum as SAEnum, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base
from app.models.enums import OutletStatusEnum


def _sa_enum(py_enum, pg_name):
    return SAEnum(
        py_enum,
        values_callable=lambda x: [e.value for e in x],
        name=pg_name,
        create_type=False,
    )


class Outlet(Base):
    __tablename__ = "outlets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    code = Column(String(10), nullable=False, unique=True)
    status = Column(_sa_enum(OutletStatusEnum, "outlet_status"), nullable=False, default=OutletStatusEnum.operational)
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True, default=None)
