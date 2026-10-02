"""QA audit checklist (Todo-Pilot §7).

Named qa_audit_* to stay apart from audit_service / audit_logs (the system
audit trail).

Flow: an admin/QA lead keeps AuditTemplates (weighted checklist items). An
auditor starts an AuditSession for an outlet — every active item is copied into
an AuditFinding (title/weight snapshot, so later template edits never rewrite
history). The auditor answers pass / fail / na, adds notes and photos, then
submits. On submit:

  - the score is computed (weighted, `na` excluded)
  - every `fail` becomes a Compliance Issue with a Task, through the normal
    issue_service.create_issue cascade (notifications + closure rules apply)
  - a fail that also failed in the previous submitted audit of the same
    outlet + template is flagged as a repeat ([Berulang], priority bumped)

Pure functions (no DB):
  compute_score, submit_problems, finding_issue_priority, find_repeats,
  monthly_trend
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.qa_audit import (
    QAAuditFinding, QAAuditPhoto, QAAuditSession, QAAuditTemplate, QAAuditTemplateItem,
)
from app.schemas.qa_audit import (
    AuditDetailResponse, AuditProgress, AuditSummaryResponse, FindingResponse, OutletScore,
    PhotoResponse, ScoresResponse, TemplateItemResponse, TemplateResponse, TrendPoint,
)
from app.services.audit_service import write_audit

RESULTS = ("pass", "fail", "na")


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------

def compute_score(findings: Iterable) -> Optional[float]:
    """Weighted % of applicable items that passed; `na`/unanswered excluded.
    None when nothing is applicable."""
    applicable = [f for f in findings if f.result in ("pass", "fail")]
    total = sum(f.weight for f in applicable)
    if total <= 0:
        return None
    passed = sum(f.weight for f in applicable if f.result == "pass")
    return round(passed * 100 / total, 1)


def submit_problems(findings: Iterable) -> List[str]:
    """Why this audit cannot be submitted yet (empty list = it can)."""
    problems: List[str] = []
    for f in findings:
        if f.result not in RESULTS:
            problems.append(f"{f.title}: not answered")
        elif f.requires_photo and f.result != "na" and not f.has_photo:
            problems.append(f"{f.title}: photo required")
    return problems


def finding_issue_priority(is_critical: bool, is_repeat: bool) -> str:
    """Priority of the Issue raised for a failed item."""
    if is_critical:
        return "critical"
    return "high" if is_repeat else "medium"


def find_repeats(failed_now: Set, failed_last_time: Optional[Set]) -> Set:
    """Template items failing now that also failed in the previous audit."""
    if not failed_last_time:
        return set()
    return set(failed_now) & set(failed_last_time)


def monthly_trend(rows: Iterable[Tuple[str, object, Optional[float]]]) -> Dict[str, List[dict]]:
    """(outlet, audit_date, score) rows → {outlet: [{month, score, audits}]}, months ascending."""
    buckets: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
    for outlet, audit_date, score in rows:
        if score is None:
            continue
        buckets[outlet][audit_date.strftime("%Y-%m")].append(score)
    return {
        outlet: [
            {"month": month, "score": round(sum(scores) / len(scores), 1), "audits": len(scores)}
            for month, scores in sorted(months.items())
        ]
        for outlet, months in buckets.items()
    }


# ---------------------------------------------------------------------------
# DB: templates
# ---------------------------------------------------------------------------

# Days to fix a failed item, by the priority of the Issue it raises.
_DUE_DAYS = {"critical": 1, "high": 3, "medium": 7}


def template_to_response(t: QAAuditTemplate) -> TemplateResponse:
    return TemplateResponse(
        id=str(t.id), name=t.name, description=t.description or "", isActive=bool(t.is_active),
        items=[
            TemplateItemResponse(
                id=str(i.id), title=i.title, category=i.category or "", weight=i.weight,
                requiresPhoto=bool(i.requires_photo), isCritical=bool(i.is_critical), orderIndex=i.order_index,
            )
            for i in t.items if i.is_active
        ],
    )


def _apply_items(t: QAAuditTemplate, items) -> None:
    existing = {str(i.id): i for i in t.items}
    keep = set()
    for idx, it in enumerate(items):
        row = existing.get(it.id) if it.id else None
        if it.id and row is None:
            raise HTTPException(status_code=422, detail=f"Unknown template item id {it.id}")
        if row is None:
            row = QAAuditTemplateItem()
            t.items.append(row)
        row.title, row.category, row.weight = it.title, it.category, it.weight
        row.requires_photo, row.is_critical = it.requiresPhoto, it.isCritical
        row.order_index, row.is_active = idx, True
        if it.id:
            keep.add(it.id)
    for item_id, row in existing.items():
        if item_id not in keep:
            row.is_active = False


def create_template(db: Session, req, actor: str) -> TemplateResponse:
    t = QAAuditTemplate(name=req.name, description=req.description)
    db.add(t)
    _apply_items(t, req.items)
    db.flush()
    write_audit(db, table_name="qa_audit_templates", record_id=str(t.id), action="create",
                new_value={"name": t.name, "items": len(req.items)}, performed_by=actor)
    db.commit()
    db.refresh(t)
    return template_to_response(t)


def update_template(db: Session, t: QAAuditTemplate, req, actor: str) -> TemplateResponse:
    if req.name is not None:
        t.name = req.name
    if req.description is not None:
        t.description = req.description
    if req.isActive is not None:
        t.is_active = req.isActive
    if req.items is not None:
        _apply_items(t, req.items)
    write_audit(db, table_name="qa_audit_templates", record_id=str(t.id), action="update",
                new_value={"name": t.name, "isActive": t.is_active}, performed_by=actor)
    db.commit()
    db.refresh(t)
    return template_to_response(t)


# ---------------------------------------------------------------------------
# DB: sessions
# ---------------------------------------------------------------------------

def photo_to_response(session_id, finding_id, p: QAAuditPhoto) -> PhotoResponse:
    base = f"/api/qa-audits/{session_id}/findings/{finding_id}/photos/{p.id}"
    return PhotoResponse(id=str(p.id), fileUrl=f"{base}/file", thumbnailUrl=f"{base}/thumbnail",
                         createdAt=p.created_at.isoformat() if p.created_at else "")


def finding_to_response(f: QAAuditFinding) -> FindingResponse:
    return FindingResponse(
        id=str(f.id), templateItemId=str(f.template_item_id) if f.template_item_id else None,
        title=f.title, category=f.category or "", weight=f.weight,
        requiresPhoto=bool(f.requires_photo), isCritical=bool(f.is_critical),
        result=f.result, notes=f.notes or "", isRepeat=bool(f.is_repeat),
        issueId=str(f.issue_id) if f.issue_id else None,
        photos=[photo_to_response(f.session_id, f.id, p) for p in f.photos],
    )


def _summary_fields(s: QAAuditSession) -> dict:
    findings = s.findings or []
    return dict(
        id=str(s.id), number=s.number, templateId=str(s.template_id), templateName=s.template_name,
        outlet=s.outlet, auditor=s.auditor_name or "", auditDate=s.audit_date.isoformat(),
        status=s.status, score=float(s.score) if s.score is not None else None,
        progress=AuditProgress(answered=sum(1 for f in findings if f.result), total=len(findings)),
        failedCount=sum(1 for f in findings if f.result == "fail"),
        repeatCount=sum(1 for f in findings if f.is_repeat),
        submittedAt=s.submitted_at.isoformat() if s.submitted_at else None,
    )


def session_to_summary(s: QAAuditSession) -> AuditSummaryResponse:
    return AuditSummaryResponse(**_summary_fields(s))


def session_to_detail(s: QAAuditSession) -> AuditDetailResponse:
    return AuditDetailResponse(**_summary_fields(s), findings=[finding_to_response(f) for f in s.findings])


def start_audit(db: Session, template: QAAuditTemplate, outlet: str, outlet_id, audit_date: date,
                auditor_id, auditor_name: str) -> AuditDetailResponse:
    from app.services.issue_service import _next_number
    if not template.is_active:
        raise HTTPException(status_code=409, detail="This audit template is inactive.")
    s = QAAuditSession(
        number=_next_number(db, "AUD", "qa_audit_number_sequences"),
        template_id=template.id, template_name=template.name,
        outlet=outlet, outlet_id=outlet_id, audit_date=audit_date,
        auditor_id=auditor_id, auditor_name=auditor_name,
    )
    for item in template.items:
        if not item.is_active:
            continue
        s.findings.append(QAAuditFinding(
            template_item_id=item.id, title=item.title, category=item.category, weight=item.weight,
            requires_photo=item.requires_photo, is_critical=item.is_critical, order_index=item.order_index,
        ))
    db.add(s)
    db.flush()
    write_audit(db, table_name="qa_audit_sessions", record_id=str(s.id), action="create",
                new_value={"number": s.number, "outlet": outlet, "template": template.name},
                performed_by=auditor_name)
    db.commit()
    db.refresh(s)
    return session_to_detail(s)


def assert_editable(s: QAAuditSession) -> None:
    if s.status != "in_progress":
        raise HTTPException(status_code=409, detail=f"Audit {s.number} is already submitted.")


def _previous_failed_items(db: Session, s: QAAuditSession):
    """Template items that failed in the last submitted audit of the same outlet + template."""
    q = db.query(QAAuditSession).filter(
        QAAuditSession.template_id == s.template_id,
        QAAuditSession.status == "submitted",
        QAAuditSession.id != s.id,
    )
    q = q.filter(QAAuditSession.outlet_id == s.outlet_id) if s.outlet_id is not None \
        else q.filter(QAAuditSession.outlet == s.outlet)
    prev = q.order_by(QAAuditSession.submitted_at.desc()).first()
    if prev is None:
        return None
    return {f.template_item_id for f in prev.findings if f.result == "fail" and f.template_item_id}


def submit_audit(db: Session, s: QAAuditSession, actor: str) -> AuditDetailResponse:
    """Score the audit, flag repeats and raise one Compliance Issue per failed item —
    all in one transaction."""
    from app.schemas.issue import CreateIssueRequest
    from app.services.issue_service import create_issue

    assert_editable(s)
    views = [_FindingView(f) for f in s.findings]
    problems = submit_problems(views)
    if problems:
        raise HTTPException(status_code=409, detail={
            "message": f"Audit {s.number} is not complete.", "problems": problems,
        })

    failed = [f for f in s.findings if f.result == "fail"]
    repeats = find_repeats({f.template_item_id for f in failed if f.template_item_id},
                           _previous_failed_items(db, s))
    for f in failed:
        f.is_repeat = f.template_item_id in repeats
        priority = finding_issue_priority(bool(f.is_critical), f.is_repeat)
        prefix = "[Berulang] " if f.is_repeat else ""
        description = "\n".join(filter(None, [
            f"Temuan audit {s.number} ({s.template_name}) tanggal {s.audit_date.isoformat()}.",
            f"Kategori: {f.category}" if f.category else "",
            f"Catatan auditor: {f.notes}" if f.notes else "",
            "Temuan ini juga gagal di audit sebelumnya." if f.is_repeat else "",
        ]))
        issue = create_issue(db, CreateIssueRequest(
            title=f"{prefix}[Audit] {f.title}"[:500],
            description=description,
            outlet=s.outlet,
            category="Compliance",
            priority=priority,
            assignee="Unassigned",
            dueDate=(date.today() + timedelta(days=_DUE_DAYS[priority])).isoformat(),
            generateTask=True,
        ), commit=False)
        f.issue_id = issue.id

    s.score = compute_score(views)
    s.status = "submitted"
    s.submitted_at = datetime.now(timezone.utc)
    write_audit(db, table_name="qa_audit_sessions", record_id=str(s.id), action="submit",
                new_value={"number": s.number, "score": float(s.score) if s.score is not None else None,
                           "failed": len(failed), "repeats": len(repeats)},
                performed_by=actor)
    db.commit()
    db.refresh(s)
    return session_to_detail(s)


class _FindingView:
    """Adapter: ORM finding → the duck type the pure rules expect."""
    def __init__(self, f: QAAuditFinding):
        self.result, self.weight, self.title = f.result, f.weight, f.title
        self.requires_photo, self.has_photo = bool(f.requires_photo), bool(f.photos)


def scores(sessions: List[QAAuditSession]) -> ScoresResponse:
    """Per-outlet latest score + monthly trend over the given submitted sessions."""
    by_outlet: Dict[str, List[QAAuditSession]] = defaultdict(list)
    for s in sessions:
        by_outlet[s.outlet].append(s)
    outlets = []
    for outlet, rows in sorted(by_outlet.items()):
        latest = max(rows, key=lambda r: (r.audit_date, r.submitted_at))
        outlets.append(OutletScore(
            outlet=outlet,
            latestScore=float(latest.score) if latest.score is not None else None,
            latestDate=latest.audit_date.isoformat(),
            audits=len(rows),
            repeatFindings=sum(1 for r in rows for f in r.findings if f.is_repeat),
        ))
    trend = monthly_trend((s.outlet, s.audit_date, float(s.score) if s.score is not None else None)
                          for s in sessions)
    return ScoresResponse(
        outlets=outlets,
        trend={o: [TrendPoint(**p) for p in pts] for o, pts in trend.items()},
    )
