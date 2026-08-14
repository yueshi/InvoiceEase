from fastapi import FastAPI

from invoicing.config import settings


def create_app() -> FastAPI:
    app = FastAPI(title="发票易 InvoiceEase")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "invoicing", "version": "0.1.0"}

    return app


app = create_app()
