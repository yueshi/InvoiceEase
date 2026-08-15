from contextlib import asynccontextmanager

from fastapi import FastAPI

from invoicing.api import api_router
from invoicing import scheduler as scheduler_mod
from invoicing.bootstrap import ensure_admin_user
from invoicing.db import SessionLocal


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as db:
        ensure_admin_user(db)
    scheduler_mod.setup_scheduler(app)
    yield
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        sched.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="发票易 InvoiceEase", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "invoicing", "version": "0.1.0"}

    app.include_router(api_router)
    return app


app = create_app()
