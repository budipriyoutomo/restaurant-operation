"""Idempotency helper (Tier 5.3-A).

Per-endpoint (not middleware) on purpose: it runs on the request's own DB
session, so it participates in the same transaction as the mutation it guards —
a middleware would need its own session and, in tests, would talk to the wrong
database.

Usage inside a mutating endpoint:

    cached = idempotency_service.get_cached(db, key, current_user.id, request)
    if cached is not None:
        return cached                      # a dict; FastAPI re-validates it
    result = <do the mutation>
    idempotency_service.store(db, key, current_user.id, request, 201, result)
    return result
"""

from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.models.idempotency import IdempotencyKey


def get_cached(db: Session, key: Optional[str], user_id, request: Request) -> Optional[dict]:
    """Return the stored response for this (key, user) if present, else None.

    If the key was first used for a different method+path, that's a client bug
    (the same key reused for a different action) → 409.
    """
    if not key:
        return None
    row = (
        db.query(IdempotencyKey)
        .filter(IdempotencyKey.key == key, IdempotencyKey.user_id == user_id)
        .first()
    )
    if row is None:
        return None
    if row.method != request.method or row.path != request.url.path:
        raise HTTPException(
            status_code=409,
            detail="Idempotency-Key already used for a different request",
        )
    return json.loads(row.response_body)


def store(db: Session, key: Optional[str], user_id, request: Request,
          status_code: int, result) -> None:
    """Persist a mutation's response under (key, user). Best-effort — a race that
    loses the PK insert just means the winning response is the one kept."""
    if not key:
        return
    body = json.dumps(jsonable_encoder(result))
    db.add(IdempotencyKey(
        key=key,
        user_id=uuid.UUID(user_id) if isinstance(user_id, str) else user_id,
        method=request.method,
        path=request.url.path,
        status_code=status_code,
        response_body=body,
    ))
    db.commit()
