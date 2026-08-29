"""Nyaya backend application entry point.

Creates and configures the FastAPI application: structured logging, request
ID middleware, consistent error handling, and the versioned API router.
Domain logic lives in the ``api``, ``core``, ``domain`` and ``llm`` packages;
this module only assembles the application.
"""

from fastapi import FastAPI

from app.api.v1.router import api_router
from app.core.config import APP_VERSION, Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.health import CheckRegistry, ConfigurationCheck
from app.core.logging import setup_logging
from app.core.request_id import RequestIDMiddleware
from app.llm.registry import create_default_registry

API_V1_PREFIX = "/api/v1"

DESCRIPTION = (
    "Backend API for Nyaya, a legal assistant over the Bharatiya Nyaya "
    "Sanhita (BNS). This is the application foundation; chat, documents, "
    "forms, search and feedback endpoints are added in later implementation "
    "phases."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        settings: Explicit settings (used by tests); defaults to the
            process-wide settings loaded from the environment.
    """
    if settings is None:
        settings = get_settings()
    setup_logging(settings)

    app = FastAPI(
        title="Nyaya API",
        description=DESCRIPTION,
        version=APP_VERSION,
    )

    # Injectable application state (no global mutable singletons in handlers).
    app.state.settings = settings
    app.state.check_registry = CheckRegistry([ConfigurationCheck(settings)])
    app.state.llm_registry = create_default_registry()

    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router, prefix=API_V1_PREFIX)
    return app


app = create_app()
