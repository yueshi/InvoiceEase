import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from invoicing.api import api_router
from invoicing.bootstrap import ensure_admin_user
from invoicing.config import settings
from invoicing.db import SessionLocal
from invoicing.mcp.server import mcp


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as db:
        ensure_admin_user(db)
    from invoicing.ops.checks import run_startup_checks

    try:
        run_startup_checks()
    except RuntimeError:
        logger = logging.getLogger("invoicing.startup")
        logger.exception("启动自检 strict 阻断")
        raise
    from invoicing import scheduler as scheduler_mod

    scheduler_mod.setup_scheduler(app)
    # OCR 引擎预热：模型首次加载 10-30s，放后台线程避免落在首个 OCR 请求上。
    # 未装 ocr extra 时 preload 内部同步判定为空转（无副作用）。
    if settings.ocr_preload:
        from invoicing.parse.ocr import preload_ocr_engine

        preload_ocr_engine()
    # MCP 挂载模式下宿主负责运行 session_manager（SDK v2 要求）。
    # 必须运行本 app 实例自带的 manager：streamable_http_app() 每次调用都会
    # 重建并覆盖 mcp 单例的 session_manager 引用，若读单例最新值会串到别的
    # app 实例上（已 run 过的 manager 再次 run 抛 RuntimeError，全量测试可复现）
    mgr = getattr(app.state, "mcp_session_manager", None)
    if mgr is not None:
        async with mgr.run():
            yield
    else:
        yield
    sched = getattr(app.state, "scheduler", None)
    if sched is not None:
        sched.shutdown(wait=False)


def create_app() -> FastAPI:
    from invoicing.ops.logging_setup import setup_logging

    try:
        setup_logging()
    except Exception:
        # 日志落盘自身失败绝不阻断业务：降级 stderr（无 handler 时 lastResort 落 stderr），继续启动
        logging.exception("日志落盘初始化失败，降级为 stderr 输出，继续启动")
    app = FastAPI(title="发票易 InvoiceEase", lifespan=lifespan)
    # /mcp 的鉴权由 SDK 认证栈负责（装配在 mcp/server.py），不再自研中间件：
    # 自研版本只认单一静态令牌，会挡在 SDK 前面把个人令牌全部 401。

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "invoicing", "version": "0.1.0"}

    app.include_router(api_router)
    # SDK v2 的 streamable_http_app 内部路由为 /mcp：挂载到根路径使
    # http://<host>:8000/mcp 直达（挂载到 /mcp 会因 Mount 要求尾斜杠产生 307，
    # 而 /mcp/ 又不匹配内部 /mcp 路由，官方客户端（不跟随重定向）将失败）
    mcp_app = mcp.streamable_http_app(json_response=True)
    # 立刻取走刚创建的 manager 绑定到本 app（后续 create_app() 会覆盖 mcp 单例引用）
    app.state.mcp_session_manager = mcp.session_manager
    app.mount("/", mcp_app)
    return app


app = create_app()
