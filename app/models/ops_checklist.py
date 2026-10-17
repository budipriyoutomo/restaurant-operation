"""Daily ops checklist models (Todo-Ops-Checklist Fase 1, migration 047).

Template → items; Schedule (template × outlet × days × due time) → one Run per
shift date, whose items are a snapshot of the template's (editing a template
never rewrites history). A failed item raises — or comments on — an Issue.
A bounded context of its own: patterns borrowed from QA audits, models not shared.
"""

import uuid

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, ForeignKey, Index, Integer, Numeric, String, Text, Time, TIMESTAMP,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped, company_key_column
from app.database import Base


class OpsChecklistTemplate(TenantScoped, Base):
    __tablename__ = "ops_checklist_templates"
    __table_args__ = (
        CheckConstraint("shift IN ('opening', 'midday', 'closing', 'other')", name="ops_checklist_templates_shift_check"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="", server_default="")
    shift = Column(String(20), nullable=False, default="other", server_default="other")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    items = relationship(
        "OpsChecklistTemplateItem", back_populates="template", cascade="all, delete-orphan",
        order_by="OpsChecklistTemplateItem.order_index", lazy="selectin",
    )


class OpsChecklistTemplateItem(TenantScoped, Base):
    __tablename__ = "ops_checklist_template_items"
    __table_args__ = (
        CheckConstraint("item_type IN ('check', 'number', 'temperature')", name="ops_checklist_template_items_type_check"),
        CheckConstraint("min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
                        name="ops_checklist_template_items_range_check"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True), ForeignKey("ops_checklist_templates.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    title = Column(String(500), nullable=False)
    section = Column(String(100), nullable=False, default="", server_default="")   # "Dapur", "Area tamu"
    item_type = Column(String(20), nullable=False, default="check", server_default="check")
    min_value = Column(Numeric(6, 1), nullable=True)
    max_value = Column(Numeric(6, 1), nullable=True)
    unit = Column(String(20), nullable=False, default="", server_default="")
    requires_photo = Column(Boolean, nullable=False, default=False, server_default="false")
    is_critical = Column(Boolean, nullable=False, default=False, server_default="false")
    # Category of the Issue raised when the item fails; NULL → system "compliance".
    issue_category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    # A temperature item bound to one monitored asset (Fase 2): limits come from
    # the asset unless set here; readings go to temperature_logs.
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    order_index = Column(Integer, nullable=False, default=0, server_default="0")
    # Removed items are deactivated, not deleted: past runs point at them.
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    template = relationship("OpsChecklistTemplate", back_populates="items")


class OpsChecklistSchedule(TenantScoped, Base):
    __tablename__ = "ops_checklist_schedules"
    __table_args__ = (
        CheckConstraint("frequency IN ('daily', 'weekly', 'monthly')", name="ops_checklist_schedules_frequency_check"),
        CheckConstraint("(frequency = 'monthly' AND day_of_month BETWEEN 1 AND 31) "
                        "OR (frequency <> 'monthly' AND day_of_month IS NULL)",
                        name="ops_checklist_schedules_day_of_month_check"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True), ForeignKey("ops_checklist_templates.id", ondelete="RESTRICT"),
                         nullable=False, index=True)
    outlet = Column(String(200), nullable=False)
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=False, index=True)
    # daily: days_of_week filter · weekly: its one weekday · monthly: day_of_month (Fase 4)
    frequency = Column(String(10), nullable=False, default="daily", server_default="daily")
    days_of_week = Column(ARRAY(Integer), nullable=False, default=list, server_default="{}")  # ISO 1–7; empty = daily
    day_of_month = Column(Integer, nullable=True)             # 1–31; a shorter month → its last day
    due_time = Column(Time, nullable=False)                   # outlet wall clock
    open_minutes_before = Column(Integer, nullable=False, default=60, server_default="60")
    grace_minutes = Column(Integer, nullable=False, default=120, server_default="120")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    deleted_at = Column(TIMESTAMP(timezone=True), nullable=True)

    template = relationship("OpsChecklistTemplate", lazy="selectin")
    outlet_ref = relationship("Outlet", lazy="selectin")


class OpsChecklistNumberSequence(TenantScoped, Base):
    __tablename__ = "ops_checklist_number_sequences"

    company_id = company_key_column()
    year = Column(Integer, primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)


class OpsChecklistRun(TenantScoped, Base):
    __tablename__ = "ops_checklist_runs"
    __table_args__ = (
        # The generator's idempotency key: one run per schedule per shift date.
        UniqueConstraint("schedule_id", "run_date", name="uq_ops_checklist_runs_schedule_date"),
        UniqueConstraint("company_id", "number", name="uq_ops_checklist_runs_company_number"),
        CheckConstraint("status IN ('pending', 'in_progress', 'submitted', 'missed')",
                        name="ops_checklist_runs_status_check"),
        Index("ix_ops_checklist_runs_outlet_date", "outlet_id", "run_date"),
        Index("ix_ops_checklist_runs_status_closes", "status", "closes_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number = Column(String(30), nullable=False)                # OPS-2026-00001
    schedule_id = Column(UUID(as_uuid=True), ForeignKey("ops_checklist_schedules.id", ondelete="RESTRICT"), nullable=False)
    template_id = Column(UUID(as_uuid=True), ForeignKey("ops_checklist_templates.id", ondelete="RESTRICT"), nullable=False)
    template_name = Column(String(200), nullable=False)
    shift = Column(String(20), nullable=False, default="other", server_default="other")
    outlet = Column(String(200), nullable=False)
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=False)
    run_date = Column(Date, nullable=False)                    # shift date at the outlet
    opens_at = Column(TIMESTAMP(timezone=True), nullable=False)
    due_at = Column(TIMESTAMP(timezone=True), nullable=False)
    closes_at = Column(TIMESTAMP(timezone=True), nullable=False)
    status = Column(String(20), nullable=False, default="pending", server_default="pending")
    is_late = Column(Boolean, nullable=False, default=False, server_default="false")
    submitted_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    submitted_by_name = Column(String(200), nullable=True)
    submitted_at = Column(TIMESTAMP(timezone=True), nullable=True)
    score = Column(Numeric(5, 1), nullable=True)
    # The "due in 30 minutes" reminder went out (Fase 3) — once per run.
    due_soon_notified_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    items = relationship(
        "OpsChecklistRunItem", back_populates="run", cascade="all, delete-orphan",
        order_by="OpsChecklistRunItem.order_index", lazy="selectin",
    )


class OpsChecklistRunItem(TenantScoped, Base):
    __tablename__ = "ops_checklist_run_items"
    __table_args__ = (
        CheckConstraint("result IS NULL OR result IN ('pass', 'fail', 'na')", name="ops_checklist_run_items_result_check"),
        CheckConstraint("item_type IN ('check', 'number', 'temperature')", name="ops_checklist_run_items_type_check"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id = Column(UUID(as_uuid=True), ForeignKey("ops_checklist_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    template_item_id = Column(UUID(as_uuid=True), ForeignKey("ops_checklist_template_items.id", ondelete="SET NULL"),
                              nullable=True, index=True)
    # Snapshot of the template item when the run was created.
    title = Column(String(500), nullable=False)
    section = Column(String(100), nullable=False, default="", server_default="")
    item_type = Column(String(20), nullable=False, default="check", server_default="check")
    min_value = Column(Numeric(6, 1), nullable=True)
    max_value = Column(Numeric(6, 1), nullable=True)
    unit = Column(String(20), nullable=False, default="", server_default="")
    requires_photo = Column(Boolean, nullable=False, default=False, server_default="false")
    is_critical = Column(Boolean, nullable=False, default=False, server_default="false")
    issue_category_id = Column(UUID(as_uuid=True), ForeignKey("categories.id", ondelete="SET NULL"), nullable=True)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    asset_name = Column(String(300), nullable=True)
    order_index = Column(Integer, nullable=False, default=0, server_default="0")
    # The answer.
    result = Column(String(10), nullable=True)                 # pass | fail | na
    value = Column(Numeric(6, 1), nullable=True)
    notes = Column(Text, nullable=False, default="", server_default="")
    corrective_action = Column(Text, nullable=True)
    recorded_at = Column(TIMESTAMP(timezone=True), nullable=True)   # device clock (offline)
    received_at = Column(TIMESTAMP(timezone=True), nullable=True)   # server clock
    recorded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    # The Issue this failure raised or was added to (dedup, keputusan #8).
    issue_id = Column(UUID(as_uuid=True), ForeignKey("issues.id", ondelete="SET NULL"), nullable=True, index=True)
    # True when this repeat failure raised the open Issue's priority — it happens once per Issue.
    escalated_issue = Column(Boolean, nullable=False, default=False, server_default="false")

    run = relationship("OpsChecklistRun", back_populates="items")
    photos = relationship(
        "OpsChecklistPhoto", back_populates="run_item", cascade="all, delete-orphan",
        order_by="OpsChecklistPhoto.created_at", lazy="selectin",
    )


class OpsChecklistPhoto(TenantScoped, Base):
    __tablename__ = "ops_checklist_photos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_item_id = Column(UUID(as_uuid=True), ForeignKey("ops_checklist_run_items.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    storage_key = Column(String(300), nullable=False)
    thumbnail_key = Column(String(300), nullable=True)
    mime_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    run_item = relationship("OpsChecklistRunItem", back_populates="photos")


class OpsDailySignoff(TenantScoped, Base):
    """A manager's "today's logs at this outlet are checked" (Fase 4, migration 051).
    One per outlet per business day; digest fixes what was signed."""
    __tablename__ = "ops_daily_signoffs"
    __table_args__ = (UniqueConstraint("outlet_id", "business_date", name="uq_ops_daily_signoffs_outlet_date"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    outlet = Column(String(200), nullable=False)
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=False)
    business_date = Column(Date, nullable=False)
    signed_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    signed_by_name = Column(String(200), nullable=False)
    signed_at = Column(TIMESTAMP(timezone=True), nullable=False)
    note = Column(Text, nullable=False, default="", server_default="")
    summary = Column(JSONB, nullable=False, default=dict, server_default="{}")
    digest = Column(String(64), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
