from pydantic import BaseModel

from invoicing.schemas.user import UserOut


class LoginRequest(BaseModel):
    username: str
    password: str


class TicketLoginRequest(BaseModel):
    """Agent 深链免登票据（一次性，见 invoicing/web_links.py）。"""

    ticket: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
