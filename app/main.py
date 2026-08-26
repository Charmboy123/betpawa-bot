"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import (
    analysis_router, bankroll_router, dashboard_router, events_router,
    health_router, proposals_router, system_router,
)
from app.core import get_logger, get_settings, setup_logging
from app.services import SchedulerService

setup_logging()
log = get_logger(__name__)

_scheduler = SchedulerService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting up")
    _scheduler.start()
    try:
        yield
    finally:
        _scheduler.stop()
        log.info("shut down")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="BetPawa CM Betting Bot", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.app_env == "development" else [],
        allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(events_router)
    app.include_router(analysis_router)
    app.include_router(proposals_router)
    app.include_router(bankroll_router)
    app.include_router(system_router)
    app.include_router(dashboard_router)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    return app


app = create_app()
