"""Chat routes — port of the NestJS ChatController.

@Controller('chat') with @UseGuards(JwtAuthGuard) on every route -> APIRouter(prefix="/chat")
with CurrentUserDep on each handler. Mounted under /api/v1 by the parent router, so the
full paths match the original (POST /api/v1/chat/sessions, etc.). The streaming endpoint
emits Server-Sent Events ("data: <json>\\n\\n", terminated by "data: [DONE]\\n\\n").
"""

import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.core.dependencies import CurrentUserDep, DBSessionDep
from app.schema.chat import CreateSessionRequest, SendMessageRequest
from app.services.chat_service import ChatService

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/sessions")
async def create_session(
    user: CurrentUserDep, db: DBSessionDep, body: CreateSessionRequest
) -> dict:
    return await ChatService(db).create_session(
        user, body.title, body.workspace_id, body.project_id
    )


@router.get("/sessions")
async def list_sessions(
    user: CurrentUserDep, db: DBSessionDep, projectId: str | None = None
) -> list[dict]:
    return await ChatService(db).list_sessions(user, projectId)


@router.delete("/sessions/{id}")
async def delete_session(user: CurrentUserDep, db: DBSessionDep, id: str) -> dict:
    return await ChatService(db).delete_session(user, id)


@router.get("/sessions/{id}/messages")
async def get_messages(user: CurrentUserDep, db: DBSessionDep, id: str) -> list[dict]:
    return await ChatService(db).get_messages(user, id)


@router.post("/sessions/{id}/messages")
async def send_message(
    user: CurrentUserDep, db: DBSessionDep, id: str, body: SendMessageRequest
) -> dict:
    return await ChatService(db).send_message(
        user, id, body.content, body.workspace_id, body.sector_id
    )


@router.post("/sessions/{id}/messages/stream")
async def stream_message(
    user: CurrentUserDep, db: DBSessionDep, id: str, body: SendMessageRequest
) -> StreamingResponse:
    chat = ChatService(db)

    async def event_stream() -> AsyncGenerator[str, None]:
        try:
            async for event in chat.stream_message(
                user, id, body.content, body.workspace_id, body.sector_id
            ):
                yield f"data: {json.dumps(event)}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as err:
            message = str(err) or "Stream failed"
            payload = {"type": "error", "data": {"message": message}}
            yield f"data: {json.dumps(payload)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
