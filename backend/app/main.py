from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.error_middleware import install_error_middleware
from app.core.logger import setup_logger
from app.core.manager import lifespan
from app.core.settings import settings
from app.core.trace_middleware import install_trace_middleware
from app.router import api_router

app = FastAPI(lifespan=lifespan, debug=settings.debug, docs_url="/api/docs")

setup_logger(settings.debug)

# Middleware nesting (outermost -> innermost): CORS -> CatchAll -> Trace -> router.
# CORS must be OUTERMOST so even 500 responses carry Access-Control-Allow-* headers;
# CatchAll converts unhandled exceptions into JSON 500s *inside* CORS, so the browser
# sees the real error instead of a misleading "CORS policy" block.
install_trace_middleware(app)  # innermost (added first)
install_error_middleware(app)  # middle (added second)
app.add_middleware(  # outermost (added last)
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "message": "RAG Backend API is running",
        "docs": "/api/docs",
        "health": "/api/v1/health",
    }


# All routes are mounted under /api/v1 (see app/router/__init__.py).
app.include_router(api_router)
