"""Food safety temperature log (Todo-Ops-Checklist Fase 2, migration 048).

Not meter_readings: those are cumulative integers (run hours) that drive
meter-based PM via MAX(value). A temperature is a decimal, often negative, and
not cumulative. Limits are snapshotted per reading, so changing an asset's
limits later never rewrites whether an old reading was in range.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, Numeric, String, Text, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped
from app.database import Base

TEMPERATURE_SOURCES = ("checklist", "manual", "sensor")


class TemperatureLog(TenantScoped, Base):
    __tablename__ = "temperature_logs"
    __table_args__ = (
        CheckConstraint("source IN ('checklist', 'manual', 'sensor')", name="temperature_logs_source_check"),
        Index("ix_temperature_logs_asset_recorded", "asset_id", "recorded_at"),
        # A device retrying the same reading is ignored (Fase 4).
        Index("uq_temperature_logs_sensor_recorded", "sensor_id", "recorded_at", unique=True,
              postgresql_where=text("sensor_id IS NOT NULL")),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    outlet = Column(String(200), nullable=False)
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    value = Column(Numeric(6, 1), nullable=False)
    min_value = Column(Numeric(6, 1), nullable=True)          # limits at the time of the reading
    max_value = Column(Numeric(6, 1), nullable=True)
    in_range = Column(Boolean, nullable=False)
    corrective_action = Column(Text, nullable=True)
    source = Column(String(20), nullable=False)
    # One log per checklist item: answering it again updates this row.
    run_item_id = Column(UUID(as_uuid=True), ForeignKey("ops_checklist_run_items.id", ondelete="SET NULL"),
                         nullable=True, unique=True)
    # The Food Safety issue this excursion raised or was added to (dedup per asset).
    issue_id = Column(UUID(as_uuid=True), ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, index=True)
    # The IoT sensor that sent it (source = sensor, Fase 4).
    sensor_id = Column(UUID(as_uuid=True), ForeignKey("temperature_sensors.id", ondelete="SET NULL"), nullable=True)
    recorded_at = Column(TIMESTAMP(timezone=True), nullable=False)
    received_at = Column(TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    asset = relationship("Asset")


class TemperatureSensor(TenantScoped, Base):
    """An IoT thermometer on one monitored asset (Fase 4, migration 052). It
    authenticates with its own key; only the SHA-256 is stored."""
    __tablename__ = "temperature_sensors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    key_prefix = Column(String(12), nullable=False)          # shown in the UI to tell keys apart
    key_hash = Column(String(64), nullable=False, unique=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    last_seen_at = Column(TIMESTAMP(timezone=True), nullable=True)
    last_value = Column(Numeric(6, 1), nullable=True)
    created_by = Column(String(200), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    revoked_at = Column(TIMESTAMP(timezone=True), nullable=True)

    asset = relationship("Asset")
