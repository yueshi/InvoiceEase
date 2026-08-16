"""常用公司字典：OCR 纠错 + 购买方归属校验（设计 V0.1）。

只作用于 confidence < 1.0 的来源（文本规则/OCR）——结构化来源（XML/XBRL，
confidence=1.0）以原件数据为准，不做纠错。门控内置在 enrich_parsed 内部，
调用方无需自行判断。"""
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
    """纠错字典 + 购买方归属校验；返回追加的校验错误。

    防御性门控：confidence 为 None 或 >= 1.0（结构化来源）时直接跳过，
    保证 XML/XBRL 原件数据永不被字典覆盖。
    """
    if parsed.confidence_score is None or parsed.confidence_score >= 1.0:
        return []
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
    # 购销方颠倒纠正：销售方税号匹配预设本司 → LLM/OCR 把本司误放销售方（破碎布局
    # 无位置依据），按「本司必为购买方」假设直接交换（进项发票场景恒成立）
    if self_infos and parsed.seller_tax_id and any(
        i.tax_id == parsed.seller_tax_id for i in self_infos
    ):
        parsed.buyer_name, parsed.seller_name = parsed.seller_name, parsed.buyer_name
        parsed.buyer_tax_id, parsed.seller_tax_id = parsed.seller_tax_id, parsed.buyer_tax_id
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
