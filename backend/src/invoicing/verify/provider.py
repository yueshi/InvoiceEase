from abc import ABC, abstractmethod

from pydantic import BaseModel

from invoicing.models import Invoice


class VerifyResult(BaseModel):
    status: str  # passed / failed / error
    detail: dict
    raw: dict


class VerifyProvider(ABC):
    @abstractmethod
    def verify(self, invoice: Invoice) -> VerifyResult: ...


def get_provider() -> VerifyProvider:
    from invoicing.verify.mock import MockVerifyProvider

    return MockVerifyProvider()
