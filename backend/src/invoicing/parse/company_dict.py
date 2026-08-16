"""常用公司字典：OCR 纠错 + 购买方归属校验（设计 V0.1）。

只作用于 confidence < 1.0 的来源（文本规则/OCR）——结构化来源（XML/XBRL）
以原件数据为准，不做纠错。调用方需自行保证该前提。
"""
import difflib

from sqlalchemy.orm import Session

from invoicing.models import CompanyInfo, CompanyKind
from invoicing.parse.schemas import ParseError, ParsedInvoice

_RATIO_THRESHOLD = 0.75
_LEN_DELTA = 4


def _best_match(name: str, infos: list[CompanyInfo]) -> CompanyInfo | None:
    best, best_ratio = None, 0.0
    for info in infos:
        if not info.name:
            continue
        ratio = difflib.SequenceMatcher(None, name, info.name).ratio()
        if ratio >= _RATIO_THRESHOLD and abs(len(name) - len(info.name)) <= _LEN_DELTA and ratio > best_ratio:
            best, best_ratio = info, ratio
    return best


def enrich_parsed(parsed: ParsedInvoice, db: Session) -> list[ParseError]:
    """纠错字典 + 购买方归属校验；返回追加的校验错误。"""
    errors: list[ParseError] = []
    infos = db.query(CompanyInfo).all()
    if not infos:
        return errors
    by_tax = {i.tax_id: i for i in infos}

    for attr in ("buyer", "seller"):
        name = getattr(parsed, f"{attr}_name") or ""
        tax_id = getattr(parsed, f"{attr}_tax_id") or ""
        if tax_id in by_tax:
            info = by_tax[tax_id]
            if info.name and name != info.name:
                setattr(parsed, f"{attr}_name", info.name)
        elif name:
            best = _best_match(name, infos)
            if best is not None:
                setattr(parsed, f"{attr}_name", best.name)

    self_infos = [i for i in infos if i.kind == CompanyKind.self.value]
    if self_infos and parsed.buyer_tax_id and all(
        i.tax_id != parsed.buyer_tax_id for i in self_infos
    ):
        errors.append(
            ParseError(
                code="BUYER_MISMATCH",
                message=f"购买方税号 {parsed.buyer_tax_id} 与预设本司不匹配",
            )
        )
    return errors
