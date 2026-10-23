import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.config import settings
from app.core.database import engine
from app.routers import (
    admin_auth,
    admin_categories,
    admin_contact,
    admin_orders,
    admin_products,
    admin_uploads,
    admin_quotes,
    categories,
    contact,
    currencies,
    orders,
    quotes,
    storefront_products,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="TechStore API", version="0.2.0")

# Storefront and admin panel run on different origins (different Next.js apps).
# Configure additional origins with CORS_ORIGINS="https://shop.example,https://admin.example".
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


# --------------------------------------------------------------------------
# One error shape for every endpoint: {"detail": "<sentence a human can read>"}.
# Both frontends read `detail` and show it verbatim, so it must always be a string.
# --------------------------------------------------------------------------

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    problems = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"] if part not in ("body", "query"))
        problems.append(f"{location}: {error['msg']}" if location else error["msg"])
    return JSONResponse(
        status_code=422,
        content={
            "detail": "; ".join(problems) or "The request body wasn't valid.",
            "errors": problems,
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=exc.headers)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    logger.warning("Database constraint violated on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=409,
        content={
            "detail": "That change conflicts with data already in the database "
            "(a duplicate slug, SKU or email). Adjust it and try again."
        },
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    logger.exception("Database error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "The database is unavailable right now. Try again in a moment."},
    )


app.include_router(admin_auth.router)
app.include_router(admin_products.router)
app.include_router(admin_categories.router)
app.include_router(admin_orders.router)
app.include_router(admin_uploads.router)
app.include_router(admin_contact.router)
app.include_router(admin_quotes.router)
app.include_router(admin_quotes.rates_router)
app.include_router(categories.router)
app.include_router(contact.router)
app.include_router(currencies.router)
app.include_router(quotes.router)
app.include_router(orders.router)
app.include_router(storefront_products.router)

# Local image storage fallback (see routers/admin_uploads.py). With S3 configured
# this directory stays empty and nothing is served from here.
media_root = Path(settings.media_root)
media_root.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=str(media_root)), name="media")


@app.get("/health")
def health_check():
    """Reports which dependencies are actually reachable, not just that the process is up."""
    database_ok = True
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        database_ok = False

    search_ok = True
    try:
        from app.services.search_service import es_client

        search_ok = bool(es_client.ping())
    except Exception:  # noqa: BLE001 - health check must never raise
        search_ok = False

    return {
        # ES is the only read model for search, so an unreachable cluster is a real
        # degradation now rather than a slower-but-working path.
        "status": "ok" if database_ok and search_ok else "degraded",
        "database": "ok" if database_ok else "unreachable",
        "search": "ok" if search_ok else "unreachable (search returns 503)",
    }
