"""Request trace middleware (port of the NestJS common/trace.middleware.ts).

Assigns/propagates an ``x-trace-id`` header per request and logs a single
completion line ``METHOD path status durationms trace=<id>`` — matching the
original ``TraceMiddleware`` so log greps and tracing remain unchanged.

The orchestrator mounts this in ``main.py`` via :func:`install_trace_middleware`.
"""

import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import logger

TRACE_HEADER = b"x-trace-id"


class TraceMiddleware:
    """Pure-ASGI middleware that injects/echoes a trace id and times requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(TRACE_HEADER)
        trace_id = incoming.decode("latin-1") if incoming else str(uuid.uuid4())

        started = time.monotonic()
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                raw_headers = message.setdefault("headers", [])
                # Echo the trace id back to the client (res.setHeader).
                raw_headers.append((TRACE_HEADER, trace_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            ms = int((time.monotonic() - started) * 1000)
            method = scope.get("method", "")
            path = scope.get("path", "")
            query = scope.get("query_string", b"")
            url = f"{path}?{query.decode('latin-1')}" if query else path
            logger.info(f"{method} {url} {status_code} {ms}ms trace={trace_id}")


def install_trace_middleware(app: "ASGIApp") -> None:
    """Mount :class:`TraceMiddleware` on a FastAPI/Starlette app.

    Imported and called by ``main.py``::

        from app.core.trace_middleware import install_trace_middleware
        install_trace_middleware(app)
    """
    app.add_middleware(TraceMiddleware)
