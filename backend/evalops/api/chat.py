"""Live chat playground API (local-only and protected by the API auth dependency).

Handlers are plain `def` so FastAPI runs generation in its thread pool; the runtime allows a
single generation at a time and answers 429 when busy.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status

from ..llm.rag_pipeline import PROMPTS
from ..schemas import (
    ChatMessageIn,
    ChatSessionCreate,
    ChatSessionDetail,
    ChatSessionOut,
    ChatStatusOut,
    ChatTurnOut,
    Page,
    SaveChatTestCaseIn,
    TestCaseOut,
)
from .deps import ChatServiceDep, get_workspace_access, require_editor

router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(get_workspace_access)])


@router.get("/status", response_model=ChatStatusOut)
def chat_status(request: Request):
    settings = request.app.state.settings
    return request.app.state.chat_runtime.status() | {
        "prompt_versions": sorted(PROMPTS),
        "max_message_chars": settings.chat_max_message_chars,
        "history_exchanges": settings.chat_history_exchanges,
    }


@router.get("/sessions", response_model=list[ChatSessionOut] | Page[ChatSessionOut])
def list_sessions(
    svc: ChatServiceDep,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int | None, Query(ge=0)] = None,
):
    return svc.list_sessions(limit=limit, offset=offset)


@router.post(
    "/sessions",
    response_model=ChatSessionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_editor)],
)
def create_session(body: ChatSessionCreate, svc: ChatServiceDep):
    return svc.create_session(body)


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
def get_session(session_id: int, svc: ChatServiceDep):
    return svc.get_session(session_id)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_editor)],
)
def delete_session(session_id: int, svc: ChatServiceDep):
    svc.delete_session(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/{session_id}/messages",
    response_model=ChatTurnOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_editor)],
)
def send_message(session_id: int, body: ChatMessageIn, svc: ChatServiceDep):
    return svc.send_message(session_id, body.content)


@router.post(
    "/messages/{message_id}/test-case",
    response_model=TestCaseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_editor)],
)
def save_test_case(message_id: int, body: SaveChatTestCaseIn, svc: ChatServiceDep):
    return svc.save_test_case(message_id, body)
