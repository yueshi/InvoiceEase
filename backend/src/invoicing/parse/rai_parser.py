"""铁路电子客票 XBRL 解析（rai 命名空间：http://xbrl.mof.gov.cn/taxonomy/2021-11-30/rai）。

字段映射来自真实样本实测（design/2026-08-16-layout-invoice-parsing-design.md §一）。
无大写金额字段 → total_amount_cn 置空，校验自动跳过 CN 项。
"""
from datetime import date
from decimal import Decimal

from lxml import etree

from invoicing.parse.schemas import ParsedInvoice

XBRL_NS = "http://www.xbrl.org/2003/instance"
# 铁路电子客票开票主体统一为国铁集团（rai XML 不含开票方字段）；可配置化留 Phase 2
RAILWAY_SELLER = "中国国家铁路集团有限公司"
RAI_NS = "http://xbrl.mof.gov.cn/taxonomy/2021-11-30/rai"


def is_rai_xbrl(root: etree._Element) -> bool:
    return root.tag == f"{{{XBRL_NS}}}xbrl" and any(
        el.tag.startswith(f"{{{RAI_NS}}}") for el in root.iter()
    )


def _rai_text(root: etree._Element, name: str) -> str | None:
    for el in root.iter(f"{{{RAI_NS}}}{name}"):
        if el.text and el.text.strip():
            return el.text.strip()
    return None


def parse_rai_xbrl(root: etree._Element) -> ParsedInvoice:
    number = _rai_text(root, "ElectronicInvoiceRailwayETicketNumber")
    issue_date = _rai_text(root, "DateOfIssue")
    if not number or not issue_date:
        raise ValueError("RAI_PARSE_ERROR: 缺少 ElectronicInvoiceRailwayETicketNumber/DateOfIssue")
    return ParsedInvoice(
        invoice_number=number,
        issue_date=date.fromisoformat(issue_date),
        amount_without_tax=Decimal(_rai_text(root, "TotalAmountExcludingTax") or "0"),
        tax_amount=Decimal(_rai_text(root, "TaxAmount") or "0"),
        total_amount=Decimal(_rai_text(root, "Fare") or "0"),
        total_amount_cn="",
        seller_name=_rai_text(root, "IssueParty") or RAILWAY_SELLER,
        seller_tax_id=_rai_text(root, "IssuePartyCode") or "",
        buyer_name=_rai_text(root, "NameOfPurchaser") or "",
        buyer_tax_id=_rai_text(root, "UnifiedSocialCreditCodeOfPurchaser") or "",
        invoice_type=_rai_text(root, "TypeOfVoucher"),
        confidence_score=1.0,
        parse_source="XML",
    )
