# 发票易 出行/铁路类发票解析实施计划（Plan G）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让解析引擎覆盖真实出行/铁路类发票：铁路电子客票 XBRL（rai 命名空间）结构化解析 + 版式 PDF/OFD 文本层规则提取，接入既有分级路由，MCP/邮件/Web 全链路自动受益。

**Architecture:** `xml_parser` 加 rai XBRL 变体分流（12306 PDF/OFD 内嵌 XML 经既有提取路径自动打通）；新增 `text_rules`（标签锚定 + 通用兜底规则）供 PDF（pypdf extract_text）与 OFD（Content.xml TextCode 坐标排序拼接）共用；router 三级路由：内嵌结构化 XML → 文本规则提取 → PDF_UNSTRUCTURED。

**Tech Stack:** lxml、pypdf、re、zipfile；ParseSource 枚举加 `PDF_TEXT`/`OFD_TEXT`（String 列，无迁移）。

**Spec:** `design/2026-08-16-layout-invoice-parsing-design.md`（V0.1，含 rai 元素清单与真实文本布局实测）。

## Global Constraints

- 合规零改动：原件存储/XML 归档/验真不自动放行/审计全复用；测试 fixture 一律合成脱敏数据，真实发票文件不入库
- 置信度：结构化（数电票/rai）= 1.0；文本规则提取 = 0.85（高于 FRD 0.8 阈值 → 自动进验真）；提取失败缺关键字段（号码/开票日期/金额）→ PDF_UNSTRUCTURED → 待复核
- 金额 Decimal 严禁 float；中文注释；测试在仓库根 `test/`；提交格式 `feat(parse): ...`
- 后端回归基线 124 不得回退；开发模式 SQLite/本地存储，无需 docker
- 真机复验文件（/Users/james/WorkBuddy/...）仅本地一次性使用，用后不落库

---

### Task 1: 铁路电子客票 XBRL 解析（rai 变体）

**Files:**
- Create: `backend/src/invoicing/parse/rai_parser.py`
- Modify: `backend/src/invoicing/parse/xml_parser.py`（变体分流）
- Create: `test/fixtures/invoices/railway_rai.xml`
- Create: `test/test_rai_parser.py`

**Interfaces:**
- Consumes: `ParsedInvoice`（parse/schemas.py）、`parse_invoice_xml`
- Produces:
  - `is_rai_xbrl(root) -> bool`（root 为 `{http://www.xbrl.org/2003/instance}xbrl` 且存在 `rai:*` 元素）
  - `parse_rai_xbrl(root) -> ParsedInvoice`（`invoice_type=TypeOfVoucher`、`total_amount_cn=""`、confidence 1.0）
  - `parse_invoice_xml` 分流：数电票优先，root 为 xbrl+rai 时走 rai

- [ ] **Step 1: 写 fixture test/fixtures/invoices/railway_rai.xml（合成脱敏，按实测 schema）**

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance"
      xmlns:rai="http://xbrl.mof.gov.cn/taxonomy/2021-11-30/rai">
  <context id="As_Of_2026_07_11">
    <entity>
      <identifier scheme="http://xbrl.mof.gov.cn">26419122537000504214</identifier>
    </entity>
    <period>
      <instant>2026-07-11</instant>
    </period>
  </context>
  <unit id="CNY"><measure>iso4217:CNY</measure></unit>
  <rai:TypeOfVoucher contextRef="As_Of_2026_07_11">电子发票（铁路电子客票）</rai:TypeOfVoucher>
  <rai:ElectronicInvoiceRailwayETicketNumber contextRef="As_Of_2026_07_11">26419122537000504214</rai:ElectronicInvoiceRailwayETicketNumber>
  <rai:DateOfIssue contextRef="As_Of_2026_07_11">2026-07-11</rai:DateOfIssue>
  <rai:TrainNumber contextRef="As_Of_2026_07_11">G1281</rai:TrainNumber>
  <rai:TravelDate contextRef="As_Of_2026_07_11">2026-07-02</rai:TravelDate>
  <rai:Fare contextRef="As_Of_2026_07_11">272.50</rai:Fare>
  <rai:TotalAmountExcludingTax contextRef="As_Of_2026_07_11">250.00</rai:TotalAmountExcludingTax>
  <rai:TaxRate contextRef="As_Of_2026_07_11">0.09</rai:TaxRate>
  <rai:TaxAmount contextRef="As_Of_2026_07_11">22.50</rai:TaxAmount>
  <rai:NameOfPurchaser contextRef="As_Of_2026_07_11">测试采购有限公司</rai:NameOfPurchaser>
  <rai:UnifiedSocialCreditCodeOfPurchaser contextRef="As_Of_2026_07_11">91310000MA1FL0B000</rai:UnifiedSocialCreditCodeOfPurchaser>
  <rai:IssueParty contextRef="As_Of_2026_07_11"></rai:IssueParty>
</xbrl>
```

- [ ] **Step 2: 写失败测试 test/test_rai_parser.py**

```python
"""铁路电子客票 rai XBRL 解析测试（合成脱敏 fixture）。"""
import io
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from invoicing.parse.rai_parser import is_rai_xbrl, parse_rai_xbrl
from invoicing.parse.validation import validate
from invoicing.parse.xbrl import extract_xml_from_pdf
from invoicing.parse.xml_parser import parse_invoice_xml
from lxml import etree

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_parse_rai_fields():
    data = (FIXTURES / "railway_rai.xml").read_bytes()
    parsed = parse_invoice_xml(data)  # 经分流走 rai 分支
    assert parsed.invoice_number == "26419122537000504214"
    assert parsed.issue_date.isoformat() == "2026-07-11"
    assert parsed.amount_without_tax == Decimal("250.00")
    assert parsed.tax_amount == Decimal("22.50")
    assert parsed.total_amount == Decimal("272.50")
    assert parsed.total_amount_cn == ""
    assert parsed.buyer_name == "测试采购有限公司"
    assert parsed.buyer_tax_id == "91310000MA1FL0B000"
    assert parsed.invoice_type == "电子发票（铁路电子客票）"
    assert parsed.confidence_score == 1.0


def test_rai_amounts_consistent():
    parsed = parse_invoice_xml((FIXTURES / "railway_rai.xml").read_bytes())
    assert validate(parsed) == []  # 250.00+22.50=272.50；无大写字段跳过 CN 校验


def test_is_rai_xbrl_detection():
    rai_root = etree.fromstring((FIXTURES / "railway_rai.xml").read_bytes())
    ei_root = etree.fromstring((FIXTURES / "dianzi.xml").read_bytes())
    assert is_rai_xbrl(rai_root) is True
    assert is_rai_xbrl(ei_root) is False


def test_rai_missing_key_fields_raises():
    bad = b'<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:rai="http://xbrl.mof.gov.cn/taxonomy/2021-11-30/rai"><rai:TypeOfVoucher>x</rai:TypeOfVoucher></xbrl>'
    with pytest.raises(ValueError, match="RAI_PARSE_ERROR"):
        parse_invoice_xml(bad)


def test_rai_inside_pdf_attachment_extracted(tmp_path):
    """12306 场景：PDF 内嵌 rai XML 附件 → 既有提取路径应能找到。"""
    rai = (FIXTURES / "railway_rai.xml").read_bytes()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_attachment("rai_issuer_test.xml", rai)
    buf = io.BytesIO()
    writer.write(buf)
    extracted = extract_xml_from_pdf(buf.getvalue())
    assert extracted is not None
    parsed = parse_invoice_xml(extracted)
    assert parsed.invoice_number == "26419122537000504214"


def test_rai_parse_direct():
    root = etree.fromstring((FIXTURES / "railway_rai.xml").read_bytes())
    parsed = parse_rai_xbrl(root)
    assert parsed.total_amount == Decimal("272.50")
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_rai_parser.py -v`
Expected: FAIL（`invoicing.parse.rai_parser` 不存在）

- [ ] **Step 4: 实现 rai_parser.py 与 xml_parser 分流**

```python
# parse/rai_parser.py
"""铁路电子客票 XBRL 解析（rai 命名空间：http://xbrl.mof.gov.cn/taxonomy/2021-11-30/rai）。

字段映射来自真实样本实测（design/2026-08-16-layout-invoice-parsing-design.md §一）。
无大写金额字段 → total_amount_cn 置空，校验自动跳过 CN 项。
"""
from datetime import date
from decimal import Decimal

from lxml import etree

from invoicing.parse.schemas import ParsedInvoice

XBRL_NS = "http://www.xbrl.org/2003/instance"
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
        seller_name=_rai_text(root, "IssueParty") or "",
        seller_tax_id=_rai_text(root, "IssuePartyCode") or "",
        buyer_name=_rai_text(root, "NameOfPurchaser") or "",
        buyer_tax_id=_rai_text(root, "UnifiedSocialCreditCodeOfPurchaser") or "",
        invoice_type=_rai_text(root, "TypeOfVoucher"),
        confidence_score=1.0,
        parse_source="XML",
    )
```

xml_parser.py 的 `parse_invoice_xml`：在 `root = etree.fromstring(data)` 之后、`number = _first_text(...)` 之前插入：

```python
    if is_rai_xbrl(root):
        from invoicing.parse.rai_parser import parse_rai_xbrl  # 延迟导入避免循环

        return parse_rai_xbrl(root)
```

（`is_rai_xbrl` 同样延迟导入或顶部导入——rai_parser 不依赖 xml_parser，可顶部导入；实现者按实际情况选择并保持一致。）

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_rai_parser.py -v`
Expected: 6 PASS

- [ ] **Step 6: 回归并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 130 passed（124 + 6）

```bash
git add backend/src/invoicing/parse/rai_parser.py backend/src/invoicing/parse/xml_parser.py \
  test/test_rai_parser.py test/fixtures/invoices/railway_rai.xml
git commit -m "feat(parse): 铁路电子客票 rai XBRL 解析变体"
```

---

### Task 2: 版式 PDF 文本层规则提取

**Files:**
- Create: `backend/src/invoicing/parse/text_rules.py`
- Create: `backend/src/invoicing/parse/pdf_text_parser.py`
- Create: `test/fixtures/invoices/pdf_layout_standard.txt`
- Create: `test/fixtures/invoices/pdf_layout_broken.txt`
- Create: `test/test_pdf_text_parser.py`

**Interfaces:**
- Consumes: `ParsedInvoice`、`pypdf.PdfReader`
- Produces:
  - `extract_pdf_text(data: bytes) -> str | None`（PdfReader 失败/无文本返回 None）
  - `extract_fields_from_text(text: str) -> ParsedInvoice | None`（缺关键字段：号码/开票日期/金额任一 → None；购销方缺失允许（置信度已表达不确定性））
  - 常量 `TEXT_CONFIDENCE = 0.85`

- [ ] **Step 1: 写 fixtures（合成脱敏文本，按实测两种布局）**

`pdf_layout_standard.txt`（及时用车/携华式标签锚定布局）：

```
电子发票（普通发票） 发票号码：26617000000309516967
开票日期：2026年07月09日
购买方信息
名称：测试采购有限公司
统一社会信用代码/纳税人识别号：91310000MA1FL0B000
销售方信息
名称：示例出行科技有限公司
统一社会信用代码/纳税人识别号：91310000MA1FL0A000
项目名称 单价 数量 金额 税率/征收率 税额
*交通运输服务*客运服务 65.48 1 65.48 3% 1.96
合    计 ¥65.48 ¥1.96
价税合计（大写） 陆拾柒元肆角肆分 （小写）¥67.44
```

`pdf_layout_broken.txt`（曹操式破碎布局：标签块与值块分离）：

```
电子发票（普通发票）旅客运输服务
发票号码：
开票日期：
购买方信息
销售方信息
名称：
统一社会信用代码/纳税人识别号：
名称：
统一社会信用代码/纳税人识别号：
项目名称 数量单价 金额 税率/征收率 税额
合 计
价税合计（大写）
 （小写）
开票人：
26327000001251594557
2026年07月09日
测试采购有限公司
91310000MA1FL0B000
示例出行科技有限公司
91310000MA1FL0A000
*交通运输服务*客运服务费 126.89 2.53 129.42
¥126.89 ¥2.53
```

- [ ] **Step 2: 写失败测试 test/test_pdf_text_parser.py**

```python
"""版式 PDF 文本层规则提取测试（合成脱敏文本）。"""
from decimal import Decimal
from pathlib import Path

from invoicing.parse.text_rules import extract_fields_from_text

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_standard_layout():
    text = (FIXTURES / "pdf_layout_standard.txt").read_text()
    parsed = extract_fields_from_text(text)
    assert parsed is not None
    assert parsed.invoice_number == "26617000000309516967"
    assert parsed.issue_date.isoformat() == "2026-07-09"
    assert parsed.amount_without_tax == Decimal("65.48")
    assert parsed.tax_amount == Decimal("1.96")
    assert parsed.total_amount == Decimal("67.44")
    assert parsed.total_amount_cn == "陆拾柒元肆角肆分"
    assert parsed.buyer_name == "测试采购有限公司"
    assert parsed.buyer_tax_id == "91310000MA1FL0B000"
    assert parsed.seller_name == "示例出行科技有限公司"
    assert parsed.seller_tax_id == "91310000MA1FL0A000"
    assert parsed.confidence_score == 0.85


def test_broken_layout():
    text = (FIXTURES / "pdf_layout_broken.txt").read_text()
    parsed = extract_fields_from_text(text)
    assert parsed is not None
    assert parsed.invoice_number == "26327000001251594557"
    assert parsed.issue_date.isoformat() == "2026-07-09"
    assert parsed.amount_without_tax == Decimal("126.89")
    assert parsed.tax_amount == Decimal("2.53")
    assert parsed.total_amount == Decimal("129.42")
    assert parsed.buyer_tax_id == "91310000MA1FL0B000"
    assert parsed.seller_tax_id == "91310000MA1FL0A000"


def test_missing_key_fields_returns_none():
    assert extract_fields_from_text("这是一段无关文本") is None
    assert extract_fields_from_text("发票号码：123 开票日期：2026年1月1日") is None  # 缺金额


def test_amounts_consistent_after_extract():
    from invoicing.parse.validation import validate

    parsed = extract_fields_from_text((FIXTURES / "pdf_layout_standard.txt").read_text())
    assert validate(parsed) == []  # 65.48+1.96=67.44 且大写一致
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_pdf_text_parser.py -v`
Expected: FAIL（`invoicing.parse.text_rules` 不存在）

- [ ] **Step 4: 实现 text_rules.py**

```python
"""版式发票文本层规则提取（标签锚定优先 + 通用数字兜底）。

真实布局实测（design/2026-08-16-layout-invoice-parsing-design.md §一）：
- 标准布局：`发票号码：xxx` / `名称：xxx` 标签与值相邻
- 破碎布局：标签块与值块分离（pypdf 文本抽取顺序所致）——兜底用
  全局首个 20 位数字（号码）、首个年月日（开票日期）、全部 18 位税号按序（购买方=第一个）

关键字段 = 号码 + 开票日期 + 不含税 + 税额 + 价税合计；任一缺失 → None。
购销方名称在破碎布局中可能不可靠提取，允许缺失（置信度 0.85 已表达不确定性）。
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from invoicing.parse.schemas import ParsedInvoice

TEXT_CONFIDENCE = 0.85

_LABEL_NO = re.compile(r"发票号码\s*[:：]?\s*(\d{20}|\d{8})")
_LABEL_DATE_CN = re.compile(r"开票日期\s*[:：]?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_LABEL_DATE_ISO = re.compile(r"开票日期\s*[:：]?\s*(\d{4}-\d{1,2}-\d{1,2})")
_LABEL_NAME = re.compile(r"名称\s*[:：]\s*([^\s:：，,]+)")
_LABEL_TAX_ID = re.compile(r"统一社会信用代码/纳税人识别号\s*[:：]\s*([0-9A-Z]{18})")
_LABEL_TOTAL = re.compile(r"合\s*计.*?¥\s*([\d.]+)\s*¥\s*([\d.]+)", re.S)
_LABEL_CN = re.compile(r"价税合计\s*[（(]大写[)）]\s*([零壹贰叁肆伍陆柒捌玖拾佰仟万亿圆整角分]+)")
_GENERIC_NO = re.compile(r"\b(\d{20})\b")
_GENERIC_DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_GENERIC_TAX_IDS = re.compile(r"[0-9A-Z]{18}")


def _to_date(m) -> str | None:
    groups = m.groups()
    if len(groups) == 3:
        return date(int(groups[0]), int(groups[1]), int(groups[2])).isoformat()
    if len(groups) == 1 and "-" in groups[0]:
        return date.fromisoformat(groups[0]).isoformat()
    return None


def extract_fields_from_text(text: str) -> ParsedInvoice | None:
    text = text.replace("　", " ")  # 全角空格归一

    number = None
    m = _LABEL_NO.search(text)
    if m:
        number = m.group(1)
    else:
        m = _GENERIC_NO.search(text)
        if m:
            number = m.group(1)

    issue_date = None
    for m in (_LABEL_DATE_CN.search(text), _LABEL_DATE_ISO.search(text), _GENERIC_DATE_CN.search(text)):
        if m:
            issue_date = _to_date(m)
            break

    total_m = _LABEL_TOTAL.search(text)
    amount_without_tax = tax_amount = total_amount = None
    if total_m:
        try:
            amount_without_tax = Decimal(total_m.group(1))
            tax_amount = Decimal(total_m.group(2))
            total_amount = amount_without_tax + tax_amount
        except InvalidOperation:
            amount_without_tax = tax_amount = total_amount = None

    if not number or not issue_date or total_amount is None:
        return None

    buyer_name = seller_name = buyer_tax_id = seller_tax_id = None
    names = _LABEL_NAME.findall(text)
    tax_ids = _LABEL_TAX_ID.findall(text)
    if names and tax_ids and len(names) >= 2 and len(tax_ids) >= 2:
        buyer_name, seller_name = names[0], names[1]
        buyer_tax_id, seller_tax_id = tax_ids[0], tax_ids[1]
    else:
        generic_ids = _GENERIC_TAX_IDS.findall(text)
        if len(generic_ids) >= 2:
            buyer_tax_id, seller_tax_id = generic_ids[0], generic_ids[1]

    total_cn = None
    m = _LABEL_CN.search(text)
    if m:
        total_cn = m.group(1)

    return ParsedInvoice(
        invoice_number=number,
        issue_date=date.fromisoformat(issue_date),
        amount_without_tax=amount_without_tax,
        tax_amount=tax_amount,
        total_amount=total_amount,
        total_amount_cn=total_cn or "",
        seller_name=seller_name or "",
        seller_tax_id=seller_tax_id or "",
        buyer_name=buyer_name or "",
        buyer_tax_id=buyer_tax_id or "",
        invoice_type=None,
        confidence_score=TEXT_CONFIDENCE,
        parse_source="PDF_TEXT",
    )
```

```python
# parse/pdf_text_parser.py
"""版式 PDF 文本层提取（pypdf extract_text）。"""
import io

from pypdf import PdfReader


def extract_pdf_text(data: bytes) -> str | None:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception:
        return None
    parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            parts.append(text)
    text = "\n".join(parts).strip()
    return text or None
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_pdf_text_parser.py -v`
Expected: 4 PASS（若破碎布局的购销方断言因规则细节失败，按「买=第一个 18 位税号、卖=第二个」意图微调实现与测试并说明）

- [ ] **Step 6: 回归并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 134 passed（130 + 4）

```bash
git add backend/src/invoicing/parse/text_rules.py backend/src/invoicing/parse/pdf_text_parser.py \
  test/test_pdf_text_parser.py test/fixtures/invoices/pdf_layout_standard.txt \
  test/fixtures/invoices/pdf_layout_broken.txt
git commit -m "feat(parse): 版式 PDF 文本层规则提取"
```

---

### Task 3: OFD 文本提取 + 路由集成

**Files:**
- Modify: `backend/src/invoicing/models/enums.py`（ParseSource 加 `PDF_TEXT`/`OFD_TEXT`）
- Create: `backend/src/invoicing/parse/ofd_text.py`
- Modify: `backend/src/invoicing/parse/router.py`（三级路由 + parse_source 一致性修正）
- Create: `test/test_ofd_text.py`

**Interfaces:**
- Consumes: `extract_fields_from_text`（Task 2）、`extract_xml_from_ofd/pdf`、`parse_invoice_xml`、`validate`
- Produces:
  - `extract_text_from_ofd(data: bytes) -> str | None`（zip 内 `Pages/*/Content.xml` 的 TextCode 按 (Y, X) 坐标排序拼接）
  - `parse_file` 三级路由：XML→结构化；PDF/OFD→内嵌 XML→文本规则→PDF_UNSTRUCTURED；`_parse_structured` 内 `parsed.parse_source = source.value`（一致性修正）

- [ ] **Step 1: 写失败测试 test/test_ofd_text.py**

```python
"""版式 OFD 文本提取测试（合成 zip 构造）。"""
import io
import zipfile

from invoicing.parse.ofd_text import extract_text_from_ofd
from invoicing.parse.router import parse_file

CONTENT_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">
  <ofd:Content>
    <ofd:Layer>
      <ofd:TextObject>
        <ofd:TextCode X="10" Y="100">电子发票（普通发票） 发票号码：26617000000309516967</ofd:TextCode>
        <ofd:TextCode X="10" Y="120">开票日期：2026年07月09日</ofd:TextCode>
        <ofd:TextCode X="10" Y="140">名称：测试采购有限公司</ofd:TextCode>
        <ofd:TextCode X="10" Y="160">统一社会信用代码/纳税人识别号：91310000MA1FL0B000</ofd:TextCode>
        <ofd:TextCode X="10" Y="180">名称：示例出行科技有限公司</ofd:TextCode>
        <ofd:TextCode X="10" Y="200">统一社会信用代码/纳税人识别号：91310000MA1FL0A000</ofd:TextCode>
        <ofd:TextCode X="10" Y="240">合    计 ¥65.48 ¥1.96</ofd:TextCode>
      </ofd:TextObject>
    </ofd:Layer>
  </ofd:Content>
</ofd:Page>
"""


def _build_ofd_layout() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016'/>")
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", CONTENT_XML)
    return buf.getvalue()


def test_extract_text_from_ofd_sorted_by_position():
    text = extract_text_from_ofd(_build_ofd_layout())
    assert text is not None
    assert "发票号码：26617000000309516967" in text
    assert "开票日期：2026年07月09日" in text


def test_parse_file_ofd_layout_text():
    from decimal import Decimal

    outcome = parse_file("OFD", _build_ofd_layout())
    assert outcome.source == "OFD_TEXT"
    assert outcome.parsed is not None
    assert outcome.parsed.invoice_number == "26617000000309516967"
    assert outcome.parsed.total_amount == Decimal("67.44")
    assert outcome.parsed.confidence_score == 0.85
    assert outcome.errors == []  # 65.48+1.96=67.44 自洽


def test_parse_file_ofd_prefers_embedded_xml():
    """内嵌结构化 XML 优先级高于文本提取。"""
    from pathlib import Path

    rai = (Path(__file__).parent / "fixtures" / "invoices" / "railway_rai.xml").read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016'/>")
        zf.writestr("Doc_0/Attachs/rai_issuer_test.xml", rai)
    outcome = parse_file("OFD", buf.getvalue())
    assert outcome.source == "OFD_XBRL"
    assert outcome.parsed.invoice_type == "电子发票（铁路电子客票）"


def test_extract_text_from_ofd_bad_zip_returns_none():
    assert extract_text_from_ofd(b"not a zip") is None
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd backend && uv run pytest ../test/test_ofd_text.py -v`
Expected: FAIL（`invoicing.parse.ofd_text` 不存在）

- [ ] **Step 3: 实现 enums 与 ofd_text.py**

enums.py 的 ParseSource 追加：`PDF_TEXT = "PDF_TEXT"`、`OFD_TEXT = "OFD_TEXT"`。

```python
# parse/ofd_text.py
"""版式 OFD 文本提取：Pages/*/Content.xml 的 TextCode 按坐标 (Y, X) 排序拼接。"""
import io
import re
import zipfile

from lxml import etree

_PAGE_RE = re.compile(r"Pages/Page_\d+/Content\.xml$")


def extract_text_from_ofd(data: bytes) -> str | None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    spans: list[tuple[float, float, str]] = []
    for name in zf.namelist():
        if not _PAGE_RE.search(name):
            continue
        try:
            root = etree.fromstring(zf.read(name))
        except Exception:
            continue
        for code in root.iter():
            if code.tag.rsplit("}", 1)[-1] != "TextCode":
                continue
            text = "".join(code.itertext()).strip()
            if not text:
                continue
            try:
                y = float(code.get("Y") or 0)
                x = float(code.get("X") or 0)
            except ValueError:
                continue
            spans.append((y, x, text))
    if not spans:
        return None
    spans.sort(key=lambda s: (s[0], s[1]))
    # 同 Y 拼一行，不同 Y 换行（近似阅读顺序）
    lines: list[str] = []
    current_y = None
    for y, _x, text in spans:
        if current_y is not None and abs(y - current_y) > 1e-6:
            lines.append("\n")
        lines.append(text)
        current_y = y
    return "".join(lines).strip() or None
```

- [ ] **Step 4: 实现 router.py 三级路由**

```python
"""parse/router.py（重写 parse_file 与 _parse_structured）"""
from invoicing.models.enums import FileType, ParseSource
from invoicing.parse.schemas import ParseError, ParseOutcome
from invoicing.parse.validation import validate
from invoicing.parse.xml_parser import parse_invoice_xml


def _parse_structured(data: bytes, source: ParseSource) -> ParseOutcome:
    try:
        parsed = parse_invoice_xml(data)
    except ValueError as e:
        return ParseOutcome(source=None, parsed=None, xml_data=data, errors=[ParseError(code="XML_PARSE_ERROR", message=str(e))])
    parsed.parse_source = source.value  # 一致性：parsed 来源与路由一致
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors, xml_data=data)


def _parse_text(text: str, source: ParseSource) -> ParseOutcome:
    from invoicing.parse.text_rules import extract_fields_from_text

    parsed = extract_fields_from_text(text)
    if parsed is None:
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    parsed.parse_source = source.value
    errors = validate(parsed)
    return ParseOutcome(source=source.value, parsed=parsed, errors=errors)


def parse_file(file_type: str, data: bytes) -> ParseOutcome:
    if file_type == FileType.XML.value:
        return _parse_structured(data, ParseSource.XML)
    if file_type == FileType.OFD.value:
        from invoicing.parse.ofd_text import extract_text_from_ofd
        from invoicing.parse.xbrl import extract_xml_from_ofd

        xml = extract_xml_from_ofd(data)
        if xml is not None:
            return _parse_structured(xml, ParseSource.OFD_XBRL)
        text = extract_text_from_ofd(data)
        if text:
            return _parse_text(text, ParseSource.OFD_TEXT)
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    if file_type == FileType.PDF.value:
        from invoicing.parse.pdf_text_parser import extract_pdf_text
        from invoicing.parse.xbrl import extract_xml_from_pdf

        xml = extract_xml_from_pdf(data)
        if xml is not None:
            return _parse_structured(xml, ParseSource.PDF_XBRL)
        text = extract_pdf_text(data)
        if text:
            return _parse_text(text, ParseSource.PDF_TEXT)
        return ParseOutcome(source=ParseSource.PDF_UNSTRUCTURED.value, parsed=None, errors=[])
    raise ValueError(f"不支持的文件类型: {file_type}")
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `cd backend && uv run pytest ../test/test_ofd_text.py -v`
Expected: 4 PASS

- [ ] **Step 6: 回归并提交**

Run: `cd backend && uv run pytest ../test -q`
Expected: 138 passed（134 + 4；若既有 parse 相关测试因 parse_source 一致性修正失败，检查断言并更新为路由语义，报告说明）

```bash
git add backend/src/invoicing/parse/ofd_text.py backend/src/invoicing/parse/router.py \
  backend/src/invoicing/models/enums.py test/test_ofd_text.py
git commit -m "feat(parse): OFD 文本提取与三级路由集成"
```

---

### Task 4: 真机复验与 Phase 2 待办更新

**Files:**
- Create: `tmp/real-invoice-verify.py`（临时脚本，用后删除）
- Modify: `design/2026-08-16-phase2-backlog.md`（关闭「打车票 XML 变体」条目，改记扫描件 OCR）
- Modify: `docs/开发环境指南.md`（可选：解析覆盖说明一行）

**Interfaces:**
- Consumes: Task 1-3 全部 + 真实文件（/Users/james/WorkBuddy/2026-08-15-22-52-26/invoices/）

- [ ] **Step 1: 写临时脚本 tmp/real-invoice-verify.py**

```python
"""真机复验：用 WorkBuddy 下载的真实发票文件跑本地解析（用后删除，文件不入库）。"""
from pathlib import Path

from invoicing.mcp.extract import extract_invoice_file

BASE = Path("/Users/james/WorkBuddy/2026-08-15-22-52-26/invoices")
files = sorted(list(BASE.rglob("*.pdf")) + list(BASE.rglob("*.ofd")))

for f in files:
    result = extract_invoice_file(str(f))
    if result.success and result.data:
        d = result.data
        print(f"OK   {f.name}")
        print(f"     号码={d.invoiceNumber} 日期={d.issueDate} 不含税={d.amountWithoutTax} "
              f"税={d.taxAmount} 合计={d.totalWithTax} 买={d.buyer.name} 卖={d.seller.name} "
              f"valid={result.validation.valid if result.validation else None}")
    else:
        print(f"FAIL {f.name}: {result.error}")
```

- [ ] **Step 2: 执行复验**

Run: `cd backend && uv run python ../tmp/real-invoice-verify.py`
Expected（按诊断推演）：
- 12306 PDF/OFD → OK（rai XBRL，号码 26419122537000504214，272.50）
- 高德 PDF ×3 → OK（文本规则，号码/金额/购销方）
- 高德 OFD → OK（OFD 文本）或 FAIL 并记录（Content.xml 文本缺失时）
若 FAIL 项与推演不符，逐一排查（文本提取为空 → 检查 Content.xml 结构；购销方错位 → 规则微调），修复后重跑直至全部 OK 或明确记录不可达项。

- [ ] **Step 3: 更新 Phase 2 待办**

design/2026-08-16-phase2-backlog.md：第 1 条「出行/服务类电子发票 XML 变体解析」改为「已完成（Plan G）：铁路客票 rai XBRL + 版式 PDF/OFD 文本规则提取；剩余：扫描件/图片 OCR、更破碎版式文本兜底」；第 3 条图片 OCR 保留。

- [ ] **Step 4: 清理并提交**

```bash
rm -f tmp/real-invoice-verify.py
git add design/2026-08-16-phase2-backlog.md
git commit -m "docs: Phase 2 待办更新（Plan G 关闭出行票解析条目，OCR 扫描件保留）"
```

---

## Plan G 验收清单（全部完成后核对）

- [ ] 后端测试全绿（124 + 6 + 4 + 4 = 138 左右）
- [ ] 真机复验：12306 PDF/OFD、高德 PDF×3、高德 OFD 全部 OK（或 FAIL 项有明确记录与原因）
- [ ] 三级路由：结构化 XML → 文本规则 → 待复核；parse_source 一致性修正
- [ ] 合规零改动：原件/归档/验真/审计路径未动
- [ ] Phase 2 待办更新
