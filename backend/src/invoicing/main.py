from contextlib import asynccontextmanager

from fastapi import FastAPI

from invoicing.api import api_router
from invoicing.bootstrap import ensure_admin_user
from invoicing.db import SessionLocal
from invoicing.mcp.auth import MCPAuthMiddleware
from invoicing.mcp.server import mcp


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as db:
        ensure_admin_user(db)
    from invoicing import scheduler as scheduler_mod

    scheduler_mod.setup_scheduler(app)
    # MCP 挂载模式下宿主负责运行 session_manager（SDK v2 要求）
    async with mcp.session_manager.run():
        yield
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        sched.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(title="发票易 InvoiceEase", lifespan=lifespan)
    app.add_middleware(MCPAuthMiddleware)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "invoicing", "version": "0.1.0"}

    app.include_router(api_router)
    app.mount("/mcp", mcp.streamable_http_app(json_response=True))
    return app


app = create_app()
