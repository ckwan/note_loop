from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import Base, SessionLocal, engine
from .errors import (
    ConflictError,
    LLMOutputError,
    NotFoundError,
    RateLimitedError,
    UnavailableError,
)
from .alert_middleware import LLMAlertMiddleware
from .review.checkpointer import close_checkpointer, get_checkpointer
from .routes import router
from .seed import seed
from .tracing import configure_tracing


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uses create_all for now. Switch to Alembic migrations when the schema settles.
    configure_tracing(get_settings())
    Base.metadata.create_all(engine)
    # Create the review checkpoint tables now. Doing it during a request hangs,
    # because one migration waits for every open transaction, including that request's.
    get_checkpointer()
    if get_settings().seed_on_start:
        with SessionLocal() as db:
            seed(db)
    yield
    close_checkpointer()


app = FastAPI(title="NoteLoop API", lifespan=lifespan)
app.include_router(router)
app.add_middleware(LLMAlertMiddleware)


def _error(status: int):
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    return handler


async def _rate_limited(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitedError)
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc)},
        headers={"Retry-After": str(exc.retry_after)},
    )


app.add_exception_handler(NotFoundError, _error(404))
app.add_exception_handler(ConflictError, _error(409))
app.add_exception_handler(LLMOutputError, _error(502))
app.add_exception_handler(UnavailableError, _error(503))
app.add_exception_handler(RateLimitedError, _rate_limited)
