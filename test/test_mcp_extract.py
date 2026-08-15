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


def test_extract_image_rejected(tmp_path):
    p = tmp_path / "photo.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0")
    result = extract_invoice_file(str(p))
    assert result.success is False
    assert "合规拒收" in result.error


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
