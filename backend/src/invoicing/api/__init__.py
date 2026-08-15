from fastapi import APIRouter

from invoicing.api import audit, auth, invoices, mailboxes, stats, users

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(invoices.router)
api_router.include_router(mailboxes.router)
api_router.include_router(stats.router)
api_router.include_router(audit.router)
api_router.include_router(users.router)
