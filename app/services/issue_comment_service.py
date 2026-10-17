"""Issue comments (Todo-Ops-Checklist P3).

add_comment is the one way to write a comment — the API (kind "user") and
services (kind "system", e.g. checklist dedup "failed again", with
commit=False inside their own transaction). The Issue's assignee is notified,
unless they wrote it.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.issue import Issue
from app.models.issue_comment import IssueComment
from app.schemas.issue import IssueCommentResponse
from app.services.audit_service import write_audit
from app.services.notification_service import notify_user
from app.services.notification_texts import L

SYSTEM_AUTHOR = "Sistem"


def to_response(c: IssueComment) -> IssueCommentResponse:
    return IssueCommentResponse(
        id=str(c.id), issueId=str(c.issue_id), body=c.body, kind=c.kind,
        authorUserId=str(c.author_user_id) if c.author_user_id else None, authorName=c.author_name,
        createdAt=c.created_at.isoformat() if c.created_at else "",
    )


def list_comments(db: Session, issue: Issue) -> List[IssueCommentResponse]:
    rows = (db.query(IssueComment).filter(IssueComment.issue_id == issue.id)
            .order_by(IssueComment.created_at, IssueComment.id).all())
    return [to_response(c) for c in rows]


def add_comment(
    db: Session,
    issue: Issue,
    body: str,
    *,
    kind: str = "user",
    author_user_id: Optional[str] = None,
    author_name: Optional[str] = None,
    actor: str = "system",
    commit: bool = True,
) -> IssueComment:
    author_uuid = uuid.UUID(str(author_user_id)) if author_user_id else None
    comment = IssueComment(
        issue_id=issue.id, body=body.strip(), kind=kind, author_user_id=author_uuid,
        author_name=(author_name or SYSTEM_AUTHOR) if kind == "user" else SYSTEM_AUTHOR,
    )
    db.add(comment)
    db.flush()
    write_audit(db, table_name="issue_comments", record_id=str(issue.id), action="comment",
                new_value={"issue": issue.number, "kind": kind, "commentId": str(comment.id)}, performed_by=actor)

    if issue.assignee_user_id and issue.assignee_user_id != author_uuid:
        who = comment.author_name
        notify_user(
            db, issue.assignee_user_id,
            title=L(f"Komentar baru: {issue.number}", f"New comment: {issue.number}"),
            message=L(f'{who} pada "{issue.title}": {comment.body[:140]}',
                      f'{who} on "{issue.title}": {comment.body[:140]}'),
            entity_type="issues", entity_id=issue.id,
        )
    if commit:
        db.commit()
        db.refresh(comment)
    return comment
