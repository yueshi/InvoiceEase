"""字体转曲 OFD 矢量渲染测试（合成 OFD，不依赖真实 cairocffi 安装时的降级路径）。"""
import io
import sys
import zipfile

from invoicing.parse.ofd_render import render_ofd_page_to_png

CONTENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ofd:Page xmlns:ofd="http://www.ofdspec.org/2016">
  <ofd:Area>
    <ofd:PhysicalBox>0 0 210 297</ofd:PhysicalBox>
    <ofd:ApplicationBox>0 0 210 297</ofd:ApplicationBox>
  </ofd:Area>
  <ofd:Content>
    <ofd:Layer>
      <ofd:DrawParam ID="1" FillColor="255 255 255"/>
      <ofd:DrawParam ID="2" FillColor="0 0 0"/>
      <ofd:PathObject Boundary="0 0 210 297" Fill="true" DrawParam="1" ID="3">
        <ofd:AbbreviatedData>M 0 0 L 210 0 L 210 297 L 0 297 C</ofd:AbbreviatedData>
      </ofd:PathObject>
      <ofd:PathObject Boundary="0 0 210 297" Fill="true" DrawParam="2" ID="4">
        <ofd:AbbreviatedData>M 20 20 L 100 20 L 100 60 L 20 60 C</ofd:AbbreviatedData>
      </ofd:PathObject>
      <ofd:PathObject Boundary="0 0 210 297" Fill="true" DrawParam="2" ID="5">
        <ofd:AbbreviatedData>M 40 80 Q 70 40 100 80 B 110 110 130 110 130 80</ofd:AbbreviatedData>
      </ofd:PathObject>
    </ofd:Layer>
  </ofd:Content>
</ofd:Page>
"""


def _build_ofd() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD xmlns:ofd='http://www.ofdspec.org/2016'/>")
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", CONTENT_XML)
    return buf.getvalue()


def test_render_produces_png_with_pixels():
    png = render_ofd_page_to_png(_build_ofd())
    assert png is not None
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG 魔数
    assert len(png) > 1000  # 非空白页


def test_render_missing_cairocffi_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "cairocffi", None)
    assert render_ofd_page_to_png(_build_ofd()) is None


def test_render_bad_zip_returns_none():
    assert render_ofd_page_to_png(b"not a zip") is None


def test_render_ofd_without_paths_returns_none():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("OFD.xml", "<ofd:OFD/>")
        zf.writestr("Doc_0/Pages/Page_0/Content.xml", "<ofd:Page xmlns:ofd='http://www.ofdspec.org/2016'/>")
    assert render_ofd_page_to_png(buf.getvalue()) is None
