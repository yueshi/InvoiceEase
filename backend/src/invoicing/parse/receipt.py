"""银行回单解析（数字员工 P3/R1）：规则提取四字段（日期/对方户名/金额/摘要）。

规则通道确定性提取；LLM 兜底由调用方用 chat_json 处理。配对建议（D5）：
金额相等 + 对方户名规范化（去公司后缀）后互相包含。
"""
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

# 通用兜底标签（模板缺失时使用——历史上这些即事实标准；模板命中时以其标签为准）
_GENERIC_LABELS = {
    "amount": ("交易金额", "付款金额", "支付金额", "金额"),
    "date": ("转账日期", "交易日期", "付款日期", "日期"),
    "party": ("对方户名", "收款方名称", "对方名称", "收款人全称", "收款人户名",
              "收款国库", "征收机关", "户名"),
    "abstract": ("摘要", "用途", "备注"),
}

# 正则按标签集缓存（模板数量有限，避免每次解析重新编译）
_RE_CACHE: dict[tuple, "re.Pattern[str]"] = {}


def _amount_re(labels: tuple[str, ...]) -> "re.Pattern[str]":
    """金额两组件：①金额关键词（禁止跨行——避免「…金额\n<产品编号>」表头误配）
    ②￥ 货币符号锚定（兜「金额 （大写）人民币… （小写）￥1,600.00」版式）。"""
    key = ("amount", labels)
    if key not in _RE_CACHE:
        alt = "|".join(labels)
        _RE_CACHE[key] = re.compile(
            rf"(?:{alt})[^0-9\n]{{0,4}}([\d,]+(?:\.\d{{1,2}})?)"
            r"|￥\s*([\d,]+(?:\.\d{1,2})?)"
        )
    return _RE_CACHE[key]


def _date_re(labels: tuple[str, ...]) -> "re.Pattern[str]":
    """回单 PDF 文本层常乱序（`转账日期： 年 月 日2026 04 20`），分隔符须可选、
    关键词与数字间距放宽到 12 个非数字字符。"""
    key = ("date", labels)
    if key not in _RE_CACHE:
        alt = "|".join(labels)
        _RE_CACHE[key] = re.compile(
            rf"(?:{alt})\D{{0,12}}(\d{{4}})\s*[年/\-]?\s*(\d{{1,2}})\s*[月/\-]?\s*(\d{{1,2}})\s*日?"
        )
    return _RE_CACHE[key]


def _party_re(labels: tuple[str, ...]) -> "re.Pattern[str]":
    """户名标签（含票证版式：收款国库/征收机关=收款方，付款人全称是本司不能当对方）。
    关键词与冒号间允许 ≤12 字标签尾巴（「征收机关名称（委托方）：」），仅在确有
    冒号时消耗——无冒号行直接从关键词后取值。"""
    key = ("party", labels)
    if key not in _RE_CACHE:
        alt = "|".join(labels)
        _RE_CACHE[key] = re.compile(
            rf"(?:{alt})(?:[^：:\n]{{0,12}}[：:])?\s*([^\n]+)"
        )
    return _RE_CACHE[key]


def _abstract_re(labels: tuple[str, ...]) -> "re.Pattern[str]":
    key = ("abstract", labels)
    if key not in _RE_CACHE:
        alt = "|".join(labels)
        _RE_CACHE[key] = re.compile(rf"(?:{alt})\s*[:：]?\s*([^\n]+)")
    return _RE_CACHE[key]


def _labels(template, field: str) -> tuple[str, ...]:
    """字段标签集：模板命中用模板的，缺失回落到通用集。"""
    if template is not None:
        tpl_labels = getattr(template, f"{field}_labels", ()) or ()
        if tpl_labels:
            return tuple(tpl_labels)
    return _GENERIC_LABELS[field]


def _party_self_labels(template) -> tuple[str, ...]:
    """本方（账户持有人）标签：模板声明优先，通用集默认含付款人全称/户名。"""
    tpl_labels = getattr(template, "party_self_labels", ()) or ()
    return tuple(tpl_labels) if tpl_labels else ("付款人全称", "付款人户名", "账户名称")
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


def infer_direction(text: str, template=None) -> str | None:
    """收付方向：收/付/None（不确定）。竖排文本先归一化再匹配关键词。

    模板可声明本行方向规则（语义关键词 > 显式回单标签）；模板未声明时
    回落通用规则（建行实测结论：语义优先于版式标签、表单类型不算方向）。
    """
    flat = re.sub(r"\s+", "", text or "")
    semantic_out = (template.direction_out if template and template.direction_out else _DIRECTION_SEMANTIC_OUT)
    semantic_in = (template.direction_in if template and template.direction_in else _DIRECTION_SEMANTIC_IN)
    label_out = (template.direction_label_out if template and template.direction_label_out else _DIRECTION_LABEL_OUT)
    label_in = (template.direction_label_in if template and template.direction_label_in else _DIRECTION_LABEL_IN)
    if any(k in flat for k in semantic_out):
        return "付"
    if any(k in flat for k in semantic_in):
        return "收"
    if any(k in flat for k in label_out):
        return "付"
    if any(k in flat for k in label_in):
        return "收"
    return None


_PROJECT_HEADER_RE = re.compile(r"(?:项目名称|计息项目|收费项目)\s*[:：]?\s*([^\s\d][^\n]*?)\s*(?:金额|利息|$)")
# 明细行模式：文字 + 所属时期起止（两段 8 位日期）+ 金额（缴款书表体）
_DETAIL_ROW_RE = re.compile(r"\d{8}\s+\d{8}\s+[\d,]+\.\d{2}")


def extract_abstract(
    text: str, amount: Decimal | None = None, template=None, table_labels: tuple[str, ...] | None = None
) -> str | None:
    """摘要提取（各版式语义摘要来源不同，多数没有「摘要」标签）：

    ① 显式标签（摘要/用途/备注）——通用回单；
    ② 明细行：含本张金额的行，行首非数字文本（险种名、利息项目）；
    ②b 明细行模式（文字+所属时期两段日期+金额）：金额被拆到多行时
       （如医疗 449.10 + 大额 8.00 = 合计 457.10）取各行行首去重拼接；
    ③ 项目表头：`项目名称 … 金额` 之间（手续费明细表）。
    """
    m = _abstract_re(_labels(template, "abstract")).search(text)
    if m:
        v = m.group(1).strip(" ：:")
        if v:
            return v[:64]
    if amount is not None:
        variants = (f"{amount:,.2f}", f"{amount:.2f}")
        for line in (text or "").splitlines():
            if not any(v in line for v in variants):
                continue
            head = re.split(r"[\d￥¥]|[\s　]{2,}", line.strip())[0].strip(" ：:")
            # 行首须为文字且非表头/金额说明（如「小写（合计）金额」「大写金额」）
            if len(head) >= 2 and not re.search(r"金额|合计|大写|小写|税（费）种|所属时期", head):
                return head[:64]
    # ②b 金额拆分到多条明细行时按行模式兜底（去重保持顺序）
    heads: list[str] = []
    for line in (text or "").splitlines():
        if not _DETAIL_ROW_RE.search(line):
            continue
        head = re.split(r"\d{8}", line.strip())[0].strip(" ：:")
        if len(head) >= 2 and head not in heads and not re.search(r"所属时期|税（费）种", head):
            heads.append(head)
    if heads:
        return "/".join(heads)[:64]
    m = _PROJECT_HEADER_RE.search(text or "")
    if m:
        v = m.group(1).strip(" ：:")
        if v:
            return v[:64]
    return None


def parse_receipt_text(
    text: str, self_accounts: set[str] | None = None, template=None
) -> dict | None:
    """规则通道（模板驱动）：四字段 + 方向 + 质量标记。

    template：银行模板（未给则用通用兜底标签集与通用方向规则）。
    关键字段（金额+对方户名）缺一 → None（不产半成品），除非命中本司账户行
    （手续费/利息等银行内部交易，本就没有对方户名——此时对方记「对应银行」
    〔模板 name〕并标 self_account_row；无模板则留空标 no_counterparty。两种情况
    都仍产出该笔，且都进人工待核对）。

    self_accounts：本司银行账号集合（账号判定比名称可靠）。
    """
    amount = None
    m = _amount_re(_labels(template, "amount")).search(text)
    if m:
        try:
            amount = Decimal((m.group(1) or m.group(2)).replace(",", ""))
        except InvalidOperation:
            amount = None
    if amount is None:
        return None

    issues: list[str] = []
    p = _party_re(_labels(template, "party")).search(text)
    if p is None:
        # 只命中本方标签（付款人户名/付款人全称…）→ 该行是账户持有人（本司），无对方户名
        sp = _party_re(_party_self_labels(template)).search(text)
        if sp is not None:
            party_line = sp.group(1)
            party = None
            hits_self_label = True
        else:
            party_line, party, hits_self_label = "", None, False
    else:
        party = _clean_party(p.group(1))
        party_line = p.group(1)
        hits_self_label = False
    # 本司账户行判定：户名行内出现本司账号 → 该行是账户持有人（本司），不是对方
    hits_self = hits_self_label or (
        bool(self_accounts) and any(acc and acc in party_line for acc in self_accounts)
    )
    if hits_self:
        # 本司账户行（手续费/利息/内部调拨等）：对方即所在银行——识别到银行模板时记
        # 「对应银行」；无模板不臆造，保持留空。两种情况都进人工队列（待核对）。
        party = template.name if template is not None else None
        issues.append("self_account_row" if party else "no_counterparty")
    elif party and _ACCOUNT_LIKE_RE.search(party_line):
        # 原始行含长数字账号（清洗后仍是裸名）→ 该行疑为账户持有人行，标记待核对
        issues.append("account_like_party")
    if not party and not hits_self:
        return None  # 无户名且非本司账户行 → 无法产出

    trade_date = None
    d = _date_re(_labels(template, "date")).search(text) or _DATE_YMD_RE.search(text)
    if d:
        trade_date = _parse_date_parts(d.group(1), d.group(2), d.group(3))
    if trade_date is None:
        issues.append("no_trade_date")
    abstract = extract_abstract(text, amount, template=template)
    from invoicing.parse.receipt_layout import validate_fields

    fields_preview = {"amount": amount, "trade_date": trade_date, "counterparty_name": party}
    for issue in validate_fields(fields_preview):
        if issue not in issues:
            issues.append(issue)
    return {
        "amount": amount,
        "counterparty_name": party,
        "trade_date": trade_date,
        "abstract": abstract,
        "direction": infer_direction(text, template=template),
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


def split_receipt_blocks(
    text: str,
    head_markers: tuple[str, ...] | None = None,
    end_markers: tuple[str, ...] | None = None,
) -> list[str]:
    """按块尾标记切分（多银行多值）；无标记的单文档文本整体一块（兼容单张回单）。

    head_markers 目前仅用于调用方语义（如版式判断），分块以 end_markers 为主——
    各行回单都以「块尾声明行」结束，最稳定。
    """
    if not text or not text.strip():
        return []
    if end_markers is None:
        from invoicing.parse.bank_templates import block_markers

        _, end_markers = block_markers(None)  # 通用兜底（多银行常见块尾措辞）
    markers = tuple(end_markers)
    pattern = re.compile("|".join(re.escape(m) for m in markers)) if markers else _BLOCK_SPLIT_RE
    chunks = [c for c in pattern.split(text) if c.strip()]
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


# 锚点算法版本：v1 = 锚点行中点切带（存在偏移缺陷）；v2 = 结构边界切带
ANCHOR_VERSION = 2

# 结构边界 token（块尾免责声明行 / 块首回单头）——每张回单各一次，位置稳定
_BLOCK_END_TOKEN = "此回单以客户真实交易为依据"
_BLOCK_HEAD_TOKEN = "单位客户专用回单"
_BAND_TOP_PAD = 8.0  # 回单头之上留白（页眉/编号行）


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

        # ① 结构边界切带（首选）：回单头（块首）与免责声明行（块尾）每张各一次，
        #    位置固定——锚点行位置随版式漂移，中点切带会偏移（真实数据复核缺陷）
        structural = None
        if locate is not None and hasattr(locate, "find_all") and h:
            markers = locate.find_all(page, _BLOCK_END_TOKEN)
            headers = locate.find_all(page, _BLOCK_HEAD_TOKEN)
            if len(markers) == len(group) and len(headers) == len(group):
                structural = [
                    (min(headers[i] + _BAND_TOP_PAD, h), markers[i]) for i in range(len(group))
                ]
        if structural is not None:
            for r, (upper, lower) in zip(group, structural):
                r["anchor"] = {
                    "bbox": _norm_band(lower, upper, h), "text": r.get("anchor_text"), "v": 2
                }
        else:
            # ② 回退：锚点 y 中点切带（结构锚点数量不匹配时）
            located = sorted((r for r in group if r.get("_y") is not None), key=lambda r: r["_y"])
            for i, r in enumerate(located):
                y = r["_y"]
                upper = (
                    (y + located[i + 1]["_y"]) / 2
                    if i + 1 < len(located)
                    else (h if h else y + 100.0)
                )
                lower = (y + located[i - 1]["_y"]) / 2 if i > 0 else 0.0
                r["anchor"] = {
                    "bbox": _norm_band(lower, upper, h), "text": r.get("anchor_text"), "v": 1
                }

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
    """回单文本按页切分：PDF 走 pypdf 文本层，图片 OCR 视为单页。

    注：坐标法整页重排（receipt_layout.reconstruct_lines）在本类 PDF 上
    经多轮尝试仍不可靠（混合排版 + 页面装饰字符 + 多张混排会互相污染），
    暂不接入主链路——拦不住的字段继续由 LLM 兜底（实测稳定）。
    后续方向：改为「局部版面查询」（用坐标在标签附近取值，只对规则失败的
    字段生效），而非整页重排。
    """
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
                # 本司账户行的对方是规则判定的「对应银行」，不接受 LLM 覆盖
                # （该场景块内本就没有其他对方标签，LLM 只可能回本司名或臆造）
                if "self_account_row" not in (fields.get("quality_issues") or []):
                    fields["counterparty_name"] = llm_party
                fields["abstract"] = data_out.get("abstract") or fields.get("abstract")
                raw_date = data_out.get("trade_date")
                if raw_date:
                    fields["trade_date"] = date.fromisoformat(raw_date)
                # LLM 有效产出 → 清除因缺字段产生的标记（保留语义标记）
                fields["quality_issues"] = [
                    i for i in (fields.get("quality_issues") or [])
                    if i in ("no_counterparty", "self_account_row")
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
    pages = _receipt_pages_from_bytes(data, kind)
    # 银行识别（全文本；命中模板 → 该行标签表与方向规则，未命中 → 通用兜底）
    from invoicing.parse.bank_templates import block_markers, detect_bank

    full_text = "\n".join(pages)
    template = detect_bank(full_text)
    _, end_markers = block_markers(template)

    results = []
    for page_no, page_text in enumerate(pages, start=1):
        for chunk in split_receipt_blocks(page_text, end_markers=end_markers):
            fields = parse_receipt_text(chunk, self_accounts=self_accounts, template=template)
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
            if template is not None:
                fields["bank_code"] = template.code
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
    """配对建议（D5 + 销项扩展）：金额相等 + 户名规范化后互相包含。

    **方向感知**（销项开票在外部执行，导入后与收款回单对账）：
    - 回单为**收**（进账）→ 候选为**销项蓝票**，匹配字段是**购买方（客户）**
    - 回单为**付**（出账）→ 候选为**进项票**（现有逻辑），匹配字段是**销售方**
    - 回单为**付**且候选为**销项红票** → 退款场景，匹配购买方

    仍排除：本司账户行（无对方户名）、对方名命中本司名称的候选（防自开票误配）。
    """
    from invoicing.models import BankReceipt, Invoice

    r = db.get(BankReceipt, receipt_id)
    if r is None or r.amount is None or not r.counterparty_name:
        return None
    from invoicing.workflow.receipts import requirement_of

    if requirement_of(r.category) == "none":
        return None  # 仅「无需发票」类跳过配对（税费/社保/银行费用/工资/调拨/财政收入）
    party = normalize_party(r.counterparty_name)
    if not party:
        return None
    self_names = _self_party_names(db)
    if any(sn and (sn in party or party in sn) for sn in self_names):
        return None

    def _hit(name: str | None) -> bool:
        norm = normalize_party(name or "")
        if not norm:
            return False
        if any(sn and (sn in norm or norm in sn) for sn in self_names):
            return False
        return party in norm or norm in party

    incoming = r.direction == "收"
    candidates = (
        db.query(Invoice).filter(Invoice.total_amount == r.amount).all()
    )
    for inv in candidates:
        if incoming:
            # 收款回单 ↔ 销项票（蓝票或红票退款前的对应收款）；匹配客户名
            if inv.invoice_direction == "output" and _hit(inv.buyer_name):
                return inv.id
        else:
            # 付款回单 ↔ 进项票；退款场景 ↔ 销项红票
            if inv.invoice_direction == "input" and _hit(inv.seller_name):
                return inv.id
            if inv.invoice_direction == "output" and inv.red_flag and _hit(inv.buyer_name):
                return inv.id
    return None


def self_account_set(db) -> set[str]:
    """本司银行账号集合：bank_accounts（启用）∪ company_infos.kind=self 的旧列（兼容）。

    账号是判定本司账户行的可靠依据（公司名可能对不上回单户名）。
    """
    from invoicing.models import BankAccount, CompanyInfo

    accounts = {
        re.sub(r"[\s\-]", "", a.account_no)
        for a in db.query(BankAccount).filter(BankAccount.enabled.is_(True)).all()
        if a.account_no
    }
    accounts |= {
        re.sub(r"[\s\-]", "", c.bank_account)
        for c in db.query(CompanyInfo).filter(CompanyInfo.kind == "self").all()
        if c.bank_account and c.bank_account.strip()
    }
    return {a for a in accounts if a}


def self_name_set(db) -> set[str]:
    """本司名称集合（归一化）：company_infos kind=self ∪ bank_accounts.account_name。

    银行账户户名可能是简称，与公司全名不同——两者都纳入名称集合。
    """
    from invoicing.models import BankAccount, CompanyInfo

    names = {
        n
        for c in db.query(CompanyInfo).filter(CompanyInfo.kind == "self").all()
        if (n := normalize_party(c.name or ""))
    }
    names |= {
        n
        for a in db.query(BankAccount).filter(BankAccount.account_name.isnot(None)).all()
        if (n := normalize_party(a.account_name or ""))
    }
    return names


def _self_party_names(db) -> set[str]:
    return self_name_set(db)
