"""WorkBuddy 识别工具测试（设计 V0.1 §三）。"""
import json
from datetime import date
from pathlib import Path

import pytest
from mcp import Client

from invoicing.mcp.extract import (
    UNSTRUCTURED,
    batch_extract_invoice_files,
    extract_invoice_file,
    validate_invoice_data,
)
from invoicing.mcp.server import mcp


@pytest.fixture(autouse=True)
def _admin_mcp_ctx(mcp_admin_auth):
    """本文件测工具**行为**（非身份）：默认以管理员身份调用，等价升级前的单令牌通道。
    身份/权限相关的回归见 test_mcp_permissions.py。"""

FIXTURES = Path(__file__).parent / "fixtures" / "invoices"


def test_extract_xml_success():
    result = extract_invoice_file(str(FIXTURES / "dianzi.xml"))
    assert result.success is True
    assert result.data.invoiceNumber == "24312000000012345678"
    assert result.data.amountWithoutTax == "909.09"
    assert result.data.totalWithTax == "1000.00"
    assert result.data.totalWithTaxCN == "壹仟元整"
    assert result.data.buyer.name == "测试采购有限公司"
    assert result.data.checkCode is None
    assert result.data.items == []
    assert result.validation.valid is True


def test_extract_plain_pdf_unstructured(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4 no data")
    result = extract_invoice_file(str(p))
    assert result.success is False
    assert result.error == UNSTRUCTURED


def test_extract_missing_file():
    with pytest.raises(ValueError, match="文件不存在"):
        extract_invoice_file("/nonexistent/x.pdf")


def test_batch_extract_mixed(tmp_path):
    ok = FIXTURES / "dianzi.xml"
    bad = tmp_path / "photo.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n")
    results = batch_extract_invoice_files([str(ok), str(bad)])
    assert len(results) == 2
    assert results[0].success is True
    assert results[1].success is False


def test_extract_runtime_error_caught(tmp_path, monkeypatch):
    # 非 ValueError 异常（如磁盘错误）也应转 success=False 而非中断
    from invoicing.mcp import extract as extract_mod

    monkeypatch.setattr(extract_mod, "parse_file", lambda kind, data: (_ for _ in ()).throw(RuntimeError("boom")))
    result = extract_invoice_file(str(FIXTURES / "dianzi.xml"))
    assert result.success is False
    assert "解析失败" in result.error


def test_batch_extract_with_missing_file(tmp_path):
    ok = FIXTURES / "dianzi.xml"
    results = batch_extract_invoice_files([str(ok), "/nonexistent/x.xml"])
    assert len(results) == 2
    assert results[0].success is True
    assert results[1].success is False
    assert "文件不存在" in results[1].error


def test_validate_non_dict_input():
    result = validate_invoice_data(None)
    assert result.valid is False
    assert any(e.code == "INVALID_FIELD" for e in result.errors)


def test_validate_ok():
    data = {
        "invoiceNumber": "24312000000012345678",
        "issueDate": "2026-08-01",
        "amountWithoutTax": "909.09",
        "taxAmount": "90.91",
        "totalWithTax": "1000.00",
        "totalWithTaxCN": "壹仟元整",
        "buyer": {"name": "A", "taxId": "T1"},
        "seller": {"name": "B", "taxId": "T2"},
    }
    assert validate_invoice_data(data).valid is True


def test_validate_total_mismatch():
    data = {
        "invoiceNumber": "N1", "issueDate": "2026-08-01",
        "amountWithoutTax": "909.10", "taxAmount": "90.91", "totalWithTax": "1000.00",
    }
    result = validate_invoice_data(data)
    assert result.valid is False
    assert any(e.code == "TOTAL_MISMATCH" for e in result.errors)


def test_validate_missing_field():
    result = validate_invoice_data({"invoiceNumber": "N1"})
    assert result.valid is False
    assert any(e.code == "MISSING_FIELD" for e in result.errors)


@pytest.mark.asyncio
async def test_in_memory_client_lists_new_tools():
    async with Client(mcp, raise_exceptions=True) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
    assert {"extract_invoice", "batch_extract_invoices", "validate_invoice"} <= names


def test_extract_image_with_ocr(tmp_path, monkeypatch):
    from invoicing.parse.ocr import OcrText

    class FakeProvider:
        def ocr_image(self, image_bytes):
            return OcrText(
                text="发票号码：26617000000309516967\n开票日期：2026年07月09日\n合 计 ¥65.48 ¥1.96\n",
                confidence=0.91,
            )

    monkeypatch.setattr("invoicing.parse.ocr.get_ocr_provider", lambda: FakeProvider())
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    result = extract_invoice_file(str(p))
    assert result.success is True
    assert result.data.invoiceNumber == "26617000000309516967"
    assert result.data.sourceFile == str(p)


def test_extract_image_ocr_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr("invoicing.parse.ocr.get_ocr_provider", lambda: None)
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    result = extract_invoice_file(str(p))
    assert result.success is False
    assert "OCR 引擎未安装" in result.error


def test_extract_image_ocr_dict_corrects_name(db, tmp_path, monkeypatch):
    """OCR 路径：预设字典对漏字名称纠错，纠错结果并入返回数据。"""
    from invoicing.models import CompanyInfo
    from invoicing.parse.ocr import OcrText

    # 预存本司；extract 内部用独立 SessionLocal，必须 commit 才可见
    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.commit()

    class FakeProvider:
        def ocr_image(self, image_bytes):
            return OcrText(
                text=(
                    "发票号码：26617000000309516967\n"
                    "开票日期：2026年07月09日\n"
                    "名称：澜鸿欣（上海）数字科技有限公司\n"
                    "统一社会信用代码/纳税人识别号：91310101MAELA36R35\n"
                    "名称：山东及时雨汽车科技有限公司西安分公司\n"
                    "统一社会信用代码/纳税人识别号：91610132MA6UY02A5U\n"
                    "合 计 ¥65.48 ¥1.96\n"
                ),
                confidence=0.91,
            )

    monkeypatch.setattr("invoicing.parse.ocr.get_ocr_provider", lambda: FakeProvider())
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    result = extract_invoice_file(str(p))
    assert result.success is True
    assert result.data.buyer.name == "澜铮鸿欣（上海）数字科技有限公司"  # OCR 漏字「铮」被纠错
    assert result.data.buyer.taxId == "91310101MAELA36R35"
    assert result.validation.valid is True  # 税号匹配本司，无 BUYER_MISMATCH


def test_extract_ofd_text_buyer_mismatch_in_validation(db, tmp_path):
    """文本路径：购买方税号与预设本司不匹配 → BUYER_MISMATCH 并入校验错误。"""
    import io
    import zipfile

    from invoicing.models import CompanyInfo

    db.add(CompanyInfo(name="澜铮鸿欣（上海）数字科技有限公司", tax_id="91310101MAELA36R35", kind="self"))
    db.commit()

    content = """<?xml version="1.0" encoding="UTF-8"?>
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
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016'/>")
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", content)
    p = tmp_path / "invoice.ofd"
    p.write_bytes(buf.getvalue())
    result = extract_invoice_file(str(p))
    assert result.success is True
    assert result.validation.valid is False
    # errors 为 list[ValidationError]（pydantic 模型），属性访问
    assert any(e.code == "BUYER_MISMATCH" for e in result.validation.errors)
