import uuid
from sqlalchemy import BigInteger, Column, String, Enum as SAEnum, TIMESTAMP, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.core.tenancy import TenantScoped
from app.database import Base
from app.models.enums import OutletStatusEnum


def _sa_enum(py_enum, pg_name):
    return SAEnum(
        py_enum,
        values_callable=lambda x: [e.value for e in x],
        name=pg_name,
        create_type=False,
    )


class Outlet(TenantScoped, Base):
    __tablename__ = "outlets"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_outlets_company_code"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    code = Column(String(10), nullable=False)
    status = Column(_sa_enum(OutletStatusEnum, "outlet_status"), nullable=False, default=OutletStatusEnum.operational)
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True, default=None)
    # IDR. NULL = use settings.APPROVAL_THRESHOLD_DEFAULT (migration 033).
    approval_threshold = Column(BigInteger, nullable=True)
