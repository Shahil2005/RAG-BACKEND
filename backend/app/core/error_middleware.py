"""Catch-all exception middleware (pure ASGI).

Converts an unhandled exception into a JSON 500 response *before* it escapes to
Starlette's outermost ServerErrorMiddleware. Because this runs INSIDE CORSMiddleware,
the 500 still passes back through CORS and receives the Access-Control-Allow-* headers
— otherwise the browser reports a real server error as a misleading "CORS policy" block.

Streaming-safe: if the response has already started (e.g. chat SSE), the exception is
re-raised unchanged so streaming behaviour is unaffected.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import logger
from app.core.settings import settings


class CatchAllExceptionMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - last-resort handler
            logger.exception(
                f"[error] unhandled exception on {scope.get('method')} {scope.get('path')}"
            )
            if response_started:
                # Response already in flight (e.g. streaming) — cannot replace it.
                raise
            content: dict[str, str] = {"detail": "Internal server error"}
            if settings.debug:
                content["error"] = f"{type(exc).__name__}: {exc}"
            await JSONResponse(status_code=500, content=content)(scope, receive, send)


def install_error_middleware(app: "ASGIApp") -> None:
    app.add_middleware(CatchAllExceptionMiddleware)
