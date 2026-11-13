"""Agent-facing API (``/api/v1``), authenticated with a per-profile API key."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..context import AppContext, get_runtime_settings
from ..errors import ApiError
from ..models import MailRequest, RequestStatus
from ..schemas import MailSubmit
from ..serializers import profile_for_agent, request_for_agent
from ..services import cancel_request, create_mail_request
from ..utils import parse_iso
from .deps import AgentAuth, get_ctx, get_db, require_agent

router = APIRouter(prefix="/api/v1", tags=["Agent API"])


@router.get("/profile", summary="Describe the profile bound to this API key")
def get_profile(auth: AgentAuth = Depends(require_agent), db: Session = Depends(get_db)) -> dict[str, Any]:
    """Sender identities, signatures and limits the agent can use."""
    return profile_for_agent(auth.profile, get_runtime_settings(db))


@router.post("/mails", status_code=202, summary="Submit an e-mail for human approval")
def submit_mail(
    data: MailSubmit,
    response: Response,
    auth: AgentAuth = Depends(require_agent),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    """Queue an e-mail. It is sent only after an operator approves it.

    Returns **202** with the new request (status `pending`). When `idempotency_key` matches an
    earlier request of this profile, that request is returned with **200** and nothing is created.
    """
    req, created = create_mail_request(ctx, db, auth.profile, auth.key, data)
    if not created:
        response.status_code = 200
    return request_for_agent(req)


@router.get("/mails", summary="List this profile's requests (feedback on earlier submissions)")
def list_mails(
    status: str | None = Query(None, description="Comma-separated statuses, e.g. 'rejected,failed'."),
    updated_since: str | None = Query(None, description="ISO-8601 timestamp; only requests changed after it."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: AgentAuth = Depends(require_agent),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    query = select(MailRequest).where(MailRequest.profile_id == auth.profile.id)
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]
        unknown = [s for s in statuses if s not in RequestStatus.ALL]
        if unknown:
            raise ApiError(422, f"Unknown status: {', '.join(unknown)}.", code="invalid_status")
        query = query.where(MailRequest.status.in_(statuses))
    if updated_since:
        try:
            since = parse_iso(updated_since)
        except ValueError:
            raise ApiError(422, "'updated_since' must be an ISO-8601 timestamp.", code="invalid_timestamp")
        query = query.where(MailRequest.updated_at > since)
    rows = db.scalars(query.order_by(MailRequest.updated_at.desc()).limit(limit + 1).offset(offset)).all()
    return {
        "items": [request_for_agent(r) for r in rows[:limit]],
        "has_more": len(rows) > limit,
    }


@router.get("/mails/{request_id}", summary="Status of one request, with the operator's comment")
def get_mail(request_id: str, auth: AgentAuth = Depends(require_agent), db: Session = Depends(get_db)) -> dict[str, Any]:
    req = db.get(MailRequest, request_id)
    if req is None or req.profile_id != auth.profile.id:
        raise ApiError(404, "Request not found.", code="not_found")
    return request_for_agent(req)


@router.post("/mails/{request_id}/cancel", summary="Withdraw a request that is still pending")
def cancel_mail(
    request_id: str,
    auth: AgentAuth = Depends(require_agent),
    db: Session = Depends(get_db),
    ctx: AppContext = Depends(get_ctx),
) -> dict[str, Any]:
    return request_for_agent(cancel_request(ctx, db, request_id, auth.profile))
