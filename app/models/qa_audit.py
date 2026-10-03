"""QA audit checklist models (Todo-Pilot §7, migration 036)."""

import uuid

from sqlalchemy import (
    Boolean, CheckConstraint, Column, Date, ForeignKey, Integer, Numeric, String, Text, TIMESTAMP, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.tenancy import TenantScoped, company_key_column
from app.database import Base


class QAAuditTemplate(TenantScoped, Base):
    __tablename__ = "qa_audit_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="", server_default="")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    items = relationship(
        "QAAuditTemplateItem", back_populates="template", cascade="all, delete-orphan",
        order_by="QAAuditTemplateItem.order_index", lazy="selectin",
    )


class QAAuditTemplateItem(TenantScoped, Base):
    __tablename__ = "qa_audit_template_items"
    __table_args__ = (CheckConstraint("weight BETWEEN 1 AND 10", name="qa_audit_template_items_weight_check"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    template_id = Column(UUID(as_uuid=True), ForeignKey("qa_audit_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(500), nullable=False)
    category = Column(String(100), nullable=False, default="", server_default="")
    weight = Column(Integer, nullable=False, default=1, server_default="1")
    requires_photo = Column(Boolean, nullable=False, default=False, server_default="false")
    is_critical = Column(Boolean, nullable=False, default=False, server_default="false")
    order_index = Column(Integer, nullable=False, default=0, server_default="0")
    # Removed items are deactivated, not deleted: past findings point at them.
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    template = relationship("QAAuditTemplate", back_populates="items")


class QAAuditNumberSequence(TenantScoped, Base):
    __tablename__ = "qa_audit_number_sequences"

    company_id = company_key_column()
    year = Column(Integer, primary_key=True)
    last_seq = Column(Integer, nullable=False, default=0)


class QAAuditSession(TenantScoped, Base):
    __tablename__ = "qa_audit_sessions"
    __table_args__ = (UniqueConstraint("company_id", "number", name="uq_qa_audit_sessions_company_number"), CheckConstraint("status IN ('in_progress', 'submitted')", name="qa_audit_sessions_status_check"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number = Column(String(30), nullable=False)            # AUD-2026-00001
    template_id = Column(UUID(as_uuid=True), ForeignKey("qa_audit_templates.id", ondelete="RESTRICT"), nullable=False)
    template_name = Column(String(200), nullable=False)
    outlet = Column(String(200), nullable=False)
    outlet_id = Column(UUID(as_uuid=True), ForeignKey("outlets.id", ondelete="RESTRICT"), nullable=True)
    auditor_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    auditor_name = Column(String(200), nullable=False, default="", server_default="")
    audit_date = Column(Date, nullable=False)
    status = Column(String(20), nullable=False, default="in_progress", server_default="in_progress")
    score = Column(Numeric(5, 1), nullable=True)
    submitted_at = Column(TIMESTAMP(timezone=True), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    findings = relationship(
        "QAAuditFinding", back_populates="session", cascade="all, delete-orphan",
        order_by="QAAuditFinding.order_index", lazy="selectin",
    )


class QAAuditFinding(TenantScoped, Base):
    __tablename__ = "qa_audit_findings"
    __table_args__ = (CheckConstraint("result IN ('pass', 'fail', 'na')", name="qa_audit_findings_result_check"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("qa_audit_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    template_item_id = Column(UUID(as_uuid=True), ForeignKey("qa_audit_template_items.id", ondelete="SET NULL"), nullable=True)
    # Snapshot of the template item at audit start.
    title = Column(String(500), nullable=False)
    category = Column(String(100), nullable=False, default="", server_default="")
    weight = Column(Integer, nullable=False, default=1, server_default="1")
    requires_photo = Column(Boolean, nullable=False, default=False, server_default="false")
    is_critical = Column(Boolean, nullable=False, default=False, server_default="false")
    order_index = Column(Integer, nullable=False, default=0, server_default="0")
    result = Column(String(10), nullable=True)                          # pass | fail | na
    notes = Column(Text, nullable=False, default="", server_default="")
    is_repeat = Column(Boolean, nullable=False, default=False, server_default="false")
    issue_id = Column(UUID(as_uuid=True), ForeignKey("issues.id", ondelete="SET NULL"), nullable=True)

    session = relationship("QAAuditSession", back_populates="findings")
    photos = relationship(
        "QAAuditPhoto", back_populates="finding", cascade="all, delete-orphan",
        order_by="QAAuditPhoto.created_at", lazy="selectin",
    )


class QAAuditPhoto(TenantScoped, Base):
    __tablename__ = "qa_audit_photos"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_id = Column(UUID(as_uuid=True), ForeignKey("qa_audit_findings.id", ondelete="CASCADE"), nullable=False, index=True)
    storage_key = Column(String(300), nullable=False)
    thumbnail_key = Column(String(300), nullable=True)
    mime_type = Column(String(100), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)

    finding = relationship("QAAuditFinding", back_populates="photos")
