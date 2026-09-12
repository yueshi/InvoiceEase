"""银行回单解析（数字员工 P3/R1）：规则提取四字段（日期/对方户名/金额/摘要）。

规则通道确定性提取；LLM 兜底由调用方用 chat_json 处理。配对建议（D5）：
金额相等 + 对方户名规范化（去公司后缀）后互相包含。
"""
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# 金额两组件：①金额关键词（禁止跨行——避免「…金额\n<产品编号>」表头误配）
# ②￥ 货币符号锚定（兜「金额 （大写）人民币… （小写）￥1,600.00」这类关键词与
# 数字间距超限的版式）。
_AMOUNT_RE = re.compile(
    r"(?:交易金额|付款金额|支付金额|金额)[^0-9\n]{0,4}([\d,]+(?:\.\d{1,2})?)"
    r"|￥\s*([\d,]+(?:\.\d{1,2})?)"
)
# 建行等回单 PDF 文本层常乱序（如 `转账日期： 年 月 日2026 04 20`——占位符在值前、
# 空格分隔），分隔符须可选、关键词与数字间距放宽到 12 个非数字字符。
_DATE_RE = re.compile(
    r"(?:转账日期|交易日期|付款日期|日期)\D{0,12}(\d{4})\s*[年/\-]?\s*(\d{1,2})\s*[月/\-]?\s*(\d{1,2})\s*日?"
)
# 户名关键词含税票回单版式（收款国库/征收机关=收款方；付款人全称是本司不能当对方）。
# 关键词与冒号间允许 ≤12 字的标签尾巴（如「征收机关名称（委托方）：」），但仅在
# 确有冒号时消耗——无冒号行（`对方户名 北京某某公司`）直接从关键词后取值。
_PARTY_RE = re.compile(
    r"(?:对方户名|收款方名称|对方名称|收款人全称|收款人户名|收款国库|征收机关|户名)"
    r"(?:[^：:\n]{0,12}[：:])?\s*([^\n]+)"
)
_ABSTRACT_RE = re.compile(r"(?:摘要|用途|备注)\s*[:：]?\s*([^\n]+)")
# CCB 打印版式：占位符「年 月 日」在前、数值在后且无日期关键词（如 `流水号：…年 月 日2026 05 12`）
_DATE_YMD_RE = re.compile(r"年\s*月\s*日\D{0,8}(\d{4})\s+(\d{1,2})\s+(\d{1,2})")

_NORMALIZE_SUFFIXES = ("股份有限公司", "有限责任公司", "有限公司", "股份公司", "公司")


def normalize_party(name: str) -> str:
    """户名规范化（D5）：去公司后缀（仅去一个）。"""
    s = (name or "").strip()
    for suf in _NORMALIZE_SUFFIXES:
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s.strip()


def _parse_date_parts(y: str, m: str, d: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


# 取值清洗（P0）：行内后续字段标签——命中即截断，保留首个实体名
_TRAILING_LABEL_RE = re.compile(r"\s+(?:账号|帐号|开户行|开户银行|税号|统一社会信用代码|收款人|付款人|金额)[：:\s]")
# 清洗后仍形如「名称+长数字账号」→ 记账号残留（未配置本司账号时的兜底识别）
_ACCOUNT_LIKE_RE = re.compile(r"[\d]{8,}|账号|帐号")

# 方向推断（P2）：优先级 = 业务语义关键词 > 显式回单方向标签。
# 语义优先的原因：页面装饰性竖排文字（贷方回单/收款人回单）会串进税票等块，
# 而「税票号码/手续费=付、利息=收」是无歧义的强证据。「电汇凭证」是表单类型
# 不是方向（转账块常同时含 贷方回单+电汇凭证），不入任何列表。
_DIRECTION_SEMANTIC_OUT = ("缴款书", "税票号码", "手续费", "工本费")
_DIRECTION_SEMANTIC_IN = ("利息", "结息", "存入")
_DIRECTION_LABEL_OUT = ("借方回单", "付款人回单", "付款回单")
_DIRECTION_LABEL_IN = ("贷方回单", "收款人回单", "收款回单")


def _clean_party(raw: str) -> str:
    """剥离捕获串里行内后续字段（账号/开户行…）：`某公司 账号： 6105…` → `某公司`。"""
    s = raw.strip().strip("，,。;；")
    m = _TRAILING_LABEL_RE.search(s)
    if m:
        s = s[: m.start()].strip()
    return s


def infer_direction(text: str) -> str | None:
    """收付方向：收/付/None（不确定）。竖排文本先归一化再匹配关键词。"""
    flat = re.sub(r"\s+", "", text or "")
    if any(k in flat for k in _DIRECTION_SEMANTIC_OUT):
        return "付"
    if any(k in flat for k in _DIRECTION_SEMANTIC_IN):
        return "收"
    if any(k in flat for k in _DIRECTION_LABEL_OUT):
        return "付"
    if any(k in flat for k in _DIRECTION_LABEL_IN):
        return "收"
    return None


def parse_receipt_text(text: str, self_accounts: set[str] | None = None) -> dict | None:
    """规则通道：四字段 + 方向 + 质量标记（P0/P1/P2）。

    关键字段（金额+对方户名）缺一 → None（不产半成品），除非命中本司账户行
    （手续费/利息等银行内部交易，本就没有对方户名——此时对方留空并记
    no_counterparty，仍产出该笔以便入账）。

    self_accounts：本司银行账号集合（账号判定比名称可靠）。
    """
    amount = None
    m = _AMOUNT_RE.search(text)
    if m:
        try:
            amount = Decimal((m.group(1) or m.group(2)).replace(",", ""))
        except InvalidOperation:
            amount = None
    if amount is None:
        return None

    issues: list[str] = []
    p = _PARTY_RE.search(text)
    party = _clean_party(p.group(1)) if p else None
    party_line = p.group(1) if p else ""
    # 本司账户行判定：户名行内出现本司账号 → 该行是账户持有人（本司），不是对方
    hits_self = bool(self_accounts) and any(acc and acc in party_line for acc in self_accounts)
    if hits_self:
        party = None
        issues.append("no_counterparty")
    elif party and _ACCOUNT_LIKE_RE.search(party_line):
        # 原始行含长数字账号（清洗后仍是裸名）→ 该行疑为账户持有人行，标记待核对
        issues.append("account_like_party")
    if not party and not hits_self:
        return None  # 无户名且非本司账户行 → 无法产出

    trade_date = None
    d = _DATE_RE.search(text) or _DATE_YMD_RE.search(text)
    if d:
        trade_date = _parse_date_parts(d.group(1), d.group(2), d.group(3))
    if trade_date is None:
        issues.append("no_trade_date")
    a = _ABSTRACT_RE.search(text)
    abstract = a.group(1).strip() if a else None
    return {
        "amount": amount,
        "counterparty_name": party,
        "trade_date": trade_date,
        "abstract": abstract,
        "direction": infer_direction(text),
        "quality_issues": issues,
    }


RECEIPT_PROMPT = (
    "你是银行回单数据提取助手。从回单文本提取四字段，输出 JSON："
    '{"trade_date": "YYYY-MM-DD 或空串", "counterparty_name": "", '
    '"amount": "数字字符串", "abstract": ""}。仅输出 JSON。'
)

# 一份 PDF 常含多张回单；各行版式不同，但块尾免责声明行是稳定的分块锚点
# （建行/工行等均打印）。按它切段，段内独立提取。
_BLOCK_SPLIT_RE = re.compile(r"此回单以客户真实交易为依据")


def split_receipt_blocks(text: str) -> list[str]:
    """按块尾免责声明行切分文本；无标记的单文档文本整体一块（兼容单张回单）。"""
    if not text or not text.strip():
        return []
    chunks = [c for c in _BLOCK_SPLIT_RE.split(text) if c.strip()]
    return chunks if chunks else [text]


def parse_receipts_text(text: str, self_accounts: set[str] | None = None) -> list[dict]:
    """多回单规则通道：分块后逐块提取，可入账的块才收录（含本司账户行内部交易）。"""
    results = []
    for chunk in split_receipt_blocks(text):
        parsed = parse_receipt_text(chunk, self_accounts=self_accounts)
        if parsed is not None:
            results.append(parsed)
    return results


_SERIAL_LABEL_RE = re.compile(
    r"(?:缴款书交易流水号|交易流水号|流水号|税票号码|凭证字号)[:：]?\s*([0-9A-Za-z]{6,})"
)
_ALNUM_RUN_RE = re.compile(r"[0-9A-Za-z]{8,}")


def anchor_token(
    text: str, amount=None, exclude: set[str] | None = None
) -> str | None:
    """块内锚点串（原件定位用），按可靠性取舍：

    ① 带标签的流水号/税票号码（最长者，页内唯一、位置稳定）；
    ② 金额（页内唯一；手续费/利息等无流水号的版式——注意此时**不能**退化取
       最长数字串：那是本司账号，页内每张回单都出现，会定位到错误位置）；
    ③ 兜底最长字母数字串（排除本司账号）。
    """
    exclude = exclude or set()
    labeled = [t for t in _SERIAL_LABEL_RE.findall(text or "") if t not in exclude]
    if labeled:
        return max(labeled, key=len)
    if amount is not None:
        return f"{amount:,.2f}"
    runs = [t for t in _ALNUM_RUN_RE.findall(text or "") if t not in exclude]
    return max(runs, key=len) if runs else None


def _normalize_token(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", s or "")


def _find_anchor_y(spans: list[tuple[str, float, float]], token: str) -> float | None:
    """在带坐标的文本片段中找锚点串（归一化后互相包含）→ 返回其 y。"""
    key = _normalize_token(token)
    if not key:
        return None
    for text, _x, y in spans:
        if key in _normalize_token(text):
            return y
    return None


def _anchor_candidates(fields: dict) -> list[str]:
    """定位候选串（按优先级）：流水号/税票号码 → 金额原文（带/不带千分位）。"""
    cands: list[str] = []
    tok = fields.get("anchor_text")
    if tok:
        cands.append(tok)
    amt = fields.get("amount")
    if amt is not None:
        cands.extend([f"{amt:,.2f}", f"{amt:.2f}"])
    return cands


def assign_anchor_bands(
    rows: list[dict],
    pages_spans: list[dict | None],
    locate=None,
) -> None:
    """为每行计算原件定位带（原地修改 rows，新增 "anchor"）。

    定位优先级：① `locate(page, token)`（PDFium 定位器，全字符坐标）按候选串
    （流水号→金额）搜索；② 退化用 pypdf 片段坐标（仅约半数片段有坐标）；
    ③ 都不行 → bbox=None（仅页码 + 锚点串，前端降级为整页提示）。

    同页多张回单按锚点 y 的**中点**切分：每行覆盖「上一张与本张的中点 → 本张与
    下一张的中点」，首/尾行延伸到页边。bbox 用归一化坐标（除以页宽高，原点在
    页面**左下**——PDF 坐标系），前端渲染时 y 需翻转为 CSS 的 top。
    """
    by_page: dict[int, list[dict]] = {}
    for r in rows:
        page = r.get("page") or 1
        info = pages_spans[page - 1] if 0 < page <= len(pages_spans) else None
        y = None
        # ① PDFium 定位器（覆盖率最高）
        if locate is not None:
            for token in _anchor_candidates(r):
                hit = locate.locate(page, token)
                if hit is not None:
                    y = (hit[0] + hit[1]) / 2
                    break
        # ② pypdf 片段坐标兜底
        if y is None and info and r.get("anchor_text"):
            y = _find_anchor_y(info["spans"], r["anchor_text"])
        r["_y"] = y
        by_page.setdefault(page, []).append(r)

    for page, group in by_page.items():
        info = pages_spans[page - 1] if 0 < page <= len(pages_spans) else None
        h = float(info["h"]) if info else None
        if not h and locate is not None:
            h = locate.page_height(page)  # pypdf 无坐标时用 PDFium 页高归一化
        located = sorted((r for r in group if r.get("_y") is not None), key=lambda r: r["_y"])
        for i, r in enumerate(located):
            y = r["_y"]
            upper = (
                (y + located[i + 1]["_y"]) / 2 if i + 1 < len(located) else (h if h else y + 100.0)
            )
            lower = (y + located[i - 1]["_y"]) / 2 if i > 0 else 0.0
            r["anchor"] = {"bbox": _norm_band(lower, upper, h), "text": r.get("anchor_text"), "v": 1}

    for r in rows:
        r.pop("_y", None)
        if "anchor" not in r:
            r["anchor"] = {"bbox": None, "text": r.get("anchor_text"), "v": 1}


def _norm_band(lower: float, upper: float, page_h: float | None) -> list[float] | None:
    if not page_h or page_h <= 0:
        return None
    y0 = max(0.0, min(lower, page_h)) / page_h
    y1 = max(0.0, min(upper, page_h)) / page_h
    if y1 <= y0:
        y0, y1 = y1, y0
    return [0.03, round(y0, 4), 0.97, round(y1, 4)]


def _receipt_pages_from_bytes(data: bytes, kind: str) -> list[str]:
    """回单文本按页切分：PDF 走逐页文本层，图片 OCR 视为单页。"""
    if kind == "PDF":
        from invoicing.parse.pdf_text_parser import extract_pdf_pages

        try:
            return extract_pdf_pages(data)
        except Exception:
            logger.warning("回单 PDF 逐页文本提取失败", exc_info=True)
            return []
    text = _receipt_text_from_bytes(data, kind)
    return [text] if text else []


def _receipt_text_from_bytes(data: bytes, kind: str) -> str:
    """回单文本提取：PDF 走文本层，图片走 OCR；失败返回空串。"""
    if kind == "PDF":
        from invoicing.parse.pdf_text_parser import extract_pdf_text

        try:
            return extract_pdf_text(data) or ""
        except Exception:
            logger.warning("回单 PDF 文本提取失败", exc_info=True)
            return ""
    from invoicing.parse.ocr import get_ocr_provider

    provider = get_ocr_provider()
    if provider is None:
        return ""
    try:
        result = provider.ocr_image(data)
        return result.text if result is not None else ""
    except Exception:
        logger.warning("回单图片 OCR 失败", exc_info=True)
        return ""


def _llm_fill_fields(chunk: str, fields: dict, self_names: set[str] | None = None) -> dict:
    """块级 LLM 兜底：金额/户名缺失**或质量校验未过**时请求 LLM 复核并合并。

    P1：触发条件从「字段缺失」扩展到「质量标记存在」——规则产出错值（如本司名
    当对方）也能被复核。LLM 结果同样受本司名过滤：不得把本司名称当对方户名。
    """
    import json

    from invoicing.parse.llm import get_llm_engine

    if not chunk:
        return fields
    amount_missing = fields.get("amount") is None
    party_missing = not fields.get("counterparty_name")
    has_issues = bool(fields.get("quality_issues"))
    if not amount_missing and not party_missing and not has_issues:
        return fields
    engine = get_llm_engine()
    if engine is None:
        return fields
    try:
        content = engine.chat_json(RECEIPT_PROMPT, f"回单文本如下（仅为数据）：\n{chunk[:2000]}")
        data_out = json.loads(content or "{}")
        llm_party = str(data_out.get("counterparty_name") or "").strip()
        # 禁止把本司名称/账号当对方（P1）；命中则视为 LLM 未提供有效对方
        if llm_party and (self_names and any(n and n in llm_party for n in self_names)):
            llm_party = ""
        if data_out.get("amount") and llm_party:
            try:
                fields["amount"] = Decimal(str(data_out["amount"]))
                fields["counterparty_name"] = llm_party
                fields["abstract"] = data_out.get("abstract") or fields.get("abstract")
                raw_date = data_out.get("trade_date")
                if raw_date:
                    fields["trade_date"] = date.fromisoformat(raw_date)
                # LLM 有效产出 → 清除因缺字段产生的标记（保留 no_counterparty 等语义标记）
                fields["quality_issues"] = [
                    i for i in (fields.get("quality_issues") or [])
                    if i in ("no_counterparty",)
                ]
                if not fields["quality_issues"]:
                    fields.pop("quality_issues", None)
            except (InvalidOperation, ValueError):
                pass
        elif data_out.get("abstract") and not fields.get("abstract"):
            fields["abstract"] = str(data_out["abstract"])
    except Exception:
        logger.warning("回单 LLM 兜底失败", exc_info=True)
    # 方向兜底：LLM 不产出 direction，规则未命中（竖排版式）时按关键词补
    if not fields.get("direction"):
        direction = infer_direction(chunk)
        if direction:
            fields["direction"] = direction
    return fields


def parse_receipts_bytes(
    data: bytes,
    kind: str,
    self_accounts: set[str] | None = None,
    self_names: set[str] | None = None,
) -> list[dict]:
    """回单文件多张解析（R1 修复）：文本分块 → 块级规则 + LLM 兜底 → 字段列表。

    一份 PDF 可含多张回单（各银行合并导出常见）；规则+LLM 均提取不出金额的块丢弃。
    无对方户名的可入账块（本司账户行内部交易）保留并带 no_counterparty 标记。
    空列表表示整份文件未识别出任何回单。

    self_accounts：本司银行账号集合；self_names：本司名称集合（LLM 过滤用）。
    """
    results = []
    for page_no, page_text in enumerate(_receipt_pages_from_bytes(data, kind), start=1):
        for chunk in split_receipt_blocks(page_text):
            fields = parse_receipt_text(chunk, self_accounts=self_accounts)
            if fields is None:
                # 规则无产出（无金额）→ 整块交 LLM 试一次
                fields = _llm_fill_fields(chunk, {}, self_names=self_names)
            else:
                fields = _llm_fill_fields(chunk, fields, self_names=self_names)
            if fields.get("amount") is None:
                continue  # 金额是入账硬前提
            if not fields.get("counterparty_name") and "no_counterparty" not in (
                fields.get("quality_issues") or []
            ):
                continue  # 既无对方也非本司账户行 → 不可入账
            fields["page"] = page_no
            token = anchor_token(chunk, amount=fields.get("amount"), exclude=self_accounts or set())
            if token:
                fields["anchor_text"] = token
            results.append(fields)
    if kind == "PDF" and results:
        from invoicing.parse.pdf_text_parser import PdfiumLocator, extract_pdf_page_spans

        locator = PdfiumLocator(data)
        assign_anchor_bands(
            results,
            extract_pdf_page_spans(data),
            locate=locator if locator.available else None,
        )
    else:
        for r in results:
            r.setdefault("anchor", {"bbox": None, "text": r.get("anchor_text"), "v": 1})
    return results


def parse_receipt_bytes(data: bytes, kind: str) -> dict:
    """单张兼容入口：多张解析结果的第一条（可能为空 dict）。"""
    rows = parse_receipts_bytes(data, kind)
    return rows[0] if rows else {}


def suggest_pair(db, receipt_id: int) -> int | None:
    """配对建议（D5）：金额相等 + 户名规范化后互相包含；返回 invoice_id 或 None。

    P2：无对方户名（本司账户行内部交易）不参与配对；候选销方命中本司名称集合
    时也排除（防本司自开票误配）。
    """
    from invoicing.models import BankReceipt, Invoice

    r = db.get(BankReceipt, receipt_id)
    if r is None or r.amount is None or not r.counterparty_name:
        return None
    party = normalize_party(r.counterparty_name)
    if not party:
        return None
    self_names = _self_party_names(db)
    if any(sn and (sn in party or party in sn) for sn in self_names):
        return None
    candidates = (
        db.query(Invoice)
        .filter(Invoice.total_amount == r.amount, Invoice.seller_name.isnot(None))
        .all()
    )
    for inv in candidates:
        norm_seller = normalize_party(inv.seller_name or "")
        if not norm_seller:
            continue
        if any(sn and (sn in norm_seller or norm_seller in sn) for sn in self_names):
            continue  # 销方是本司 → 不配对
        if party in norm_seller or norm_seller in party:
            return inv.id
    return None


def self_account_set(db) -> set[str]:
    """本司银行账号集合（company_infos kind=self 的 bank_account，非空）。"""
    from invoicing.models import CompanyInfo

    return {
        c.bank_account.strip()
        for c in db.query(CompanyInfo).filter(CompanyInfo.kind == "self").all()
        if c.bank_account and c.bank_account.strip()
    }


def self_name_set(db) -> set[str]:
    """本司名称集合（归一化后，company_infos kind=self）。"""
    from invoicing.models import CompanyInfo

    return {
        n
        for c in db.query(CompanyInfo).filter(CompanyInfo.kind == "self").all()
        if (n := normalize_party(c.name or ""))
    }


def _self_party_names(db) -> set[str]:
    return self_name_set(db)
