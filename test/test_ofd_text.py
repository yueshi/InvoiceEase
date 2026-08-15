"""版式 OFD 文本提取测试（合成 zip 构造）。"""
import io
import zipfile
from decimal import Decimal
from pathlib import Path

from invoicing.parse.ofd_text import extract_text_from_ofd
from invoicing.parse.router import parse_file

CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
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
    outcome = parse_file("OFD", _build_ofd_layout())
    assert outcome.source == "OFD_TEXT"
    assert outcome.parsed is not None
    assert outcome.parsed.invoice_number == "26617000000309516967"
    assert outcome.parsed.total_amount == Decimal("67.44")
    assert outcome.parsed.confidence_score == 0.85
    assert outcome.errors == []  # 65.48+1.96=67.44 自洽


def test_parse_file_ofd_prefers_embedded_xml():
    """内嵌结构化 XML 优先级高于文本提取。"""
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
