from fastapi import APIRouter

from invoicing.api import (
    audit,
    auth,
    bank_accounts,
    company_infos,
    expenses,
    invoices,
    mailboxes,
    mcp_tokens,
    receipts,
    reports,
    stats,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(invoices.router)
api_router.include_router(mailboxes.router)
api_router.include_router(mcp_tokens.router)
api_router.include_router(stats.router)
api_router.include_router(audit.router)
api_router.include_router(users.router)
api_router.include_router(company_infos.router)
api_router.include_router(bank_accounts.router)
api_router.include_router(expenses.router)
api_router.include_router(reports.router)
api_router.include_router(receipts.router)
