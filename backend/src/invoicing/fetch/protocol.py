from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class RawAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class RawMailMessage:
    uid: int
    message_id: str | None
    subject: str
    sender: str
    provider_message_id: str | None = None
    attachments: list[RawAttachment] = field(default_factory=list)


class MailFetcher(ABC):
    @abstractmethod
    def fetch_new(self, last_uid: int) -> list[RawMailMessage]: ...

    @abstractmethod
    def mark_seen(self, uids: list[int]) -> None: ...
