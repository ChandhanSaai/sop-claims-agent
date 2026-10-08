import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.auth import require_token
from app.api.schemas import (
    ChatRequest,
    ChatResponse,
    OutboxResponse,
    SessionCreateRequest,
    SessionCreateResponse,
    TraceResponse,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])


def _store(request: Request):
    return request.app.state.sessions


def _service(request: Request):
    return request.app.state.service


def _get_session(request: Request, session_id: str):
    try:
        return _store(request).get(session_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="unknown or expired session") from None


@router.post("/session", response_model=SessionCreateResponse)
def create_session(body: SessionCreateRequest, request: Request):
    session = _service(request).start(body.scenario)
    _store(request).put(session)
    return SessionCreateResponse(
        session_id=session.id, greeting=session.transcript[-1].text, state=session.snapshot()
    )


@router.post("/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request):
    session = _get_session(request, body.session_id)
    result = _service(request).chat(session, body.message)
    log.info("turn handled", extra={"session_id": session.id, "turn": session.turn})
    return ChatResponse(reply=result.reply, state=session.snapshot(), trace=result.trace)


@router.get("/session/{session_id}/outbox", response_model=OutboxResponse)
def outbox(session_id: str, request: Request):
    session = _get_session(request, session_id)
    return OutboxResponse(emails=_service(request).outbox(session))


@router.get("/session/{session_id}/trace", response_model=TraceResponse)
def trace(session_id: str, request: Request):
    session = _get_session(request, session_id)
    return TraceResponse(turns=session.traces)
