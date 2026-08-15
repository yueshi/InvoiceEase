from fastapi import APIRouter

from invoicing.api import auth, invoices, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(invoices.router)
api_router.include_router(users.router)
