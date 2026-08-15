from enum import Enum


class Role(str, Enum):
    employee = "employee"
    finance_staff = "finance_staff"
    finance_manager = "finance_manager"
    admin = "admin"


class InvoiceStatus(str, Enum):
    receiving = "receiving"
    received = "received"
    parsing = "parsing"
    parsed = "parsed"
    verifying = "verifying"
    pending_submit = "pending_submit"
    pending_review = "pending_review"
    blocked = "blocked"
    rejected = "rejected"
    submitted = "submitted"
    archived = "archived"


class VerifyStatus(str, Enum):
    pending = "pending"
    passed = "passed"
    failed = "failed"


class ParseSource(str, Enum):
    XML = "XML"
    OFD_XBRL = "OFD_XBRL"
    PDF_XBRL = "PDF_XBRL"
    PDF_UNSTRUCTURED = "PDF_UNSTRUCTURED"


class FileType(str, Enum):
    PDF = "PDF"
    OFD = "OFD"
    XML = "XML"


class AuditAction(str, Enum):
    FETCH = "FETCH"
    PARSE = "PARSE"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    REJECT_REPLY = "REJECT_REPLY"
    CONFIG_CHANGE = "CONFIG_CHANGE"
    REVERIFY = "REVERIFY"
    INGEST = "INGEST"
