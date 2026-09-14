from __future__ import annotations

import logging
from secrets import compare_digest

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import settings
from .cors_config import build_cors_options
from .db import Base, SessionLocal, engine, ensure_runtime_schema
from .api.accounts import router as accounts_router
from .api.analytics import router as analytics_router
from .api.exceptions import router as exceptions_router
from .api.imports import router as imports_router
from .api.metrics import router as metrics_router
from .api.net_worth import router as net_worth_router
from .api.rules import router as rules_router
from .api.statements import router as statements_router
from .api.transfers import router as transfers_router
from .api.transactions import router as transactions_router


app = FastAPI(title="Finance Tracker API")
logger = logging.getLogger(__name__)
cors_origins, cors_allow_credentials = build_cors_options(settings.cors_allowed_origins)


def _request_has_valid_admin_token(request: Request, expected_token: str) -> bool:
    header_token = request.headers.get("x-admin-token", "")
    auth = request.headers.get("authorization", "")
    bearer_token = ""
    if auth:
        parts = auth.strip().split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            bearer_token = parts[1].strip()

    return compare_digest(header_token, expected_token) or compare_digest(
        bearer_token,
        expected_token,
    )


@app.on_event("startup")
def startup() -> None:
    settings.ensure_paths()
    Base.metadata.create_all(bind=engine)
    ensure_runtime_schema()

    try:
        from .services.rules_engine import bootstrap_default_rules_if_needed

        with SessionLocal() as db:
            bootstrap_result = bootstrap_default_rules_if_needed(db)
            if bootstrap_result.default_rules_created or bootstrap_result.transactions_updated:
                db.commit()
    except Exception:
        # Startup should not fail due to rule bootstrap.
        logger.warning(
            "Startup rules bootstrap failed; continuing without blocking API.", exc_info=True
        )

    # Best-effort catch-up for any queued imports (e.g., if the parser was added later
    # or the API restarted mid-import).
    try:
        import threading

        from .services.import_processing import process_all_queued_imports

        threading.Thread(
            target=process_all_queued_imports, kwargs={"max_batches": 50}, daemon=True
        ).start()

        from .services.net_worth import backfill_net_worth_artifacts_background

        threading.Thread(target=backfill_net_worth_artifacts_background, daemon=True).start()
    except Exception:
        # Startup should not fail due to background parsing.
        logger.warning(
            "Startup background workers failed to start; continuing without blocking API.",
            exc_info=True,
        )


@app.middleware("http")
async def admin_guard(request: Request, call_next):
    token = settings.api_admin_token
    if token and token != "change-me":
        if (
            request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and request.url.path != "/api/health"
        ):
            if not _request_has_valid_admin_token(request, token):
                return JSONResponse(status_code=401, content={"detail": "Invalid admin token"})
    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.include_router(accounts_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(transactions_router, prefix="/api")
app.include_router(statements_router, prefix="/api")
app.include_router(imports_router, prefix="/api")
app.include_router(exceptions_router, prefix="/api")
app.include_router(rules_router, prefix="/api")
app.include_router(metrics_router, prefix="/api")
app.include_router(net_worth_router, prefix="/api")
app.include_router(transfers_router, prefix="/api")
