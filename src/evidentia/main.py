"""Application factory. Run with ``uvicorn --factory evidentia.main:create_app``."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from evidentia import __version__
from evidentia.api import health
from evidentia.api.router import build_api_router
from evidentia.core.config import Settings, get_settings
from evidentia.core.errors import register_exception_handlers
from evidentia.core.logging import configure_logging
from evidentia.core.metrics import metrics_app
from evidentia.core.middleware import RequestContextMiddleware
from evidentia.core.tasks import CeleryDispatcher, set_dispatcher
from evidentia.core.tracing import instrument_app
from evidentia.db.session import Database
from evidentia.integrations.bin_lookup import build_bin_lookup
from evidentia.integrations.storage import build_storage
from evidentia.modules.admin.store import CachedGatewaySource
from evidentia.modules.notifications.hub import NotificationHub

log = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = Database(settings.database)
        app.state.redis = Redis.from_url(settings.redis.url, decode_responses=True)
        app.state.storage = build_storage(settings.storage)
        app.state.ai = CachedGatewaySource(settings)
        app.state.http = httpx.AsyncClient(timeout=10.0)
        app.state.bin_lookup = build_bin_lookup(settings.cards, app.state.http)
        app.state.notifications = NotificationHub(app.state.redis)
        app.state.notifications.start()
        set_dispatcher(CeleryDispatcher())
        log.info("startup", environment=settings.environment, version=__version__)
        try:
            yield
        finally:
            await app.state.notifications.stop()
            await app.state.http.aclose()
            await app.state.redis.aclose()
            await app.state.db.dispose()
            log.info("shutdown")

    app = FastAPI(
        title="Evidentia API",
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(build_api_router(settings.api_prefix))
    if settings.observability.metrics_enabled:
        app.mount("/metrics", metrics_app())
    instrument_app(app, settings.observability, environment=settings.environment)
    return app
