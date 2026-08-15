"""字体转曲 OFD 的矢量渲染：解析 PathObject/AbbreviatedData 贝塞尔路径，cairocffi 画整页 PNG。

背景：部分 OFD（如高德打车票）把文字画成字形轮廓路径，无 TextCode 文本层、
无整页图片——文本规则与图片 OCR 都失效。本模块把整页路径按 DrawParam 填充色
渲染为 PNG，供 PaddleOCR 识别。

依赖：cairocffi（optional extra ocr；系统需 libcairo，macOS `brew install cairo`、
Linux `apt install libcairo2`）。缺依赖时返回 None，路由落待复核。
"""
import io
import re
import zipfile

from lxml import etree

_PAGE_RE = re.compile(r"Pages/Page_\d+/Content\.xml$")
_TOKEN_RE = re.compile(r"([MLQBC])\s*([-\d.\s]+?)(?=[MLQBC]|$)")


def _numbers(text: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text)]


def _parse_color(value: str | None, default: tuple = (0.0, 0.0, 0.0)) -> tuple:
    if not value:
        return default
    parts = _numbers(value)
    if len(parts) >= 3:
        return (parts[0] / 255.0, parts[1] / 255.0, parts[2] / 255.0)
    return default


def _trace_path(ctx, data: str) -> None:
    """OFD 路径语法：M 移动 / L 直线 / Q 二次贝塞尔 / B 三次贝塞尔 / C 闭合。"""
    for m in _TOKEN_RE.finditer(data):
        op = m.group(1)
        nums = _numbers(m.group(2))
        if op == "M":
            ctx.move_to(nums[0], nums[1])
            nums = nums[2:]
        elif op == "L":
            i = 0
            while i + 1 < len(nums):
                ctx.line_to(nums[i], nums[i + 1])
                i += 2
        elif op == "Q":
            i = 0
            while i + 3 < len(nums):
                x0, y0 = ctx.get_current_point()
                ctx.curve_to(
                    x0 + 2 / 3 * (nums[i] - x0),
                    y0 + 2 / 3 * (nums[i + 1] - y0),
                    nums[i + 2] + 2 / 3 * (nums[i] - nums[i + 2]),
                    nums[i + 3] + 2 / 3 * (nums[i + 1] - nums[i + 3]),
                    nums[i + 2],
                    nums[i + 3],
                )
                i += 4
        elif op == "B":
            i = 0
            while i + 5 < len(nums):
                ctx.curve_to(nums[i], nums[i + 1], nums[i + 2], nums[i + 3], nums[i + 4], nums[i + 5])
                i += 6
        elif op == "C":
            ctx.close_path()


def render_ofd_page_to_png(ofd_bytes: bytes, px_per_mm: float = 6.0) -> bytes | None:
    try:
        import cairocffi as cairo
    except ImportError:
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(ofd_bytes))
    except zipfile.BadZipFile:
        return None

    page_name = next((n for n in zf.namelist() if _PAGE_RE.search(n)), None)
    if page_name is None:
        return None
    try:
        root = etree.fromstring(zf.read(page_name))
    except Exception:
        return None

    # 页面尺寸（mm）
    area_text = None
    for el in root.iter():
        ln = el.tag.rsplit("}", 1)[-1]
        if ln == "ApplicationBox":
            area_text = el.text
            break
        if ln == "PhysicalBox":
            area_text = el.text
    if not area_text:
        return None
    dims = _numbers(area_text)
    if len(dims) < 4:
        return None
    width_mm, height_mm = dims[2], dims[3]

    # DrawParam 填充色（ID → RGB 0-1）：颜色可能是属性，也可能是 <FillColor Value="..."/>
    # 子元素；DrawParam 可能定义在 Content.xml 或 DocumentRes/PublicRes.xml——全量收集
    draw_params: dict[str, tuple] = {}
    for name in zf.namelist():
        if not name.lower().endswith(".xml"):
            continue
        try:
            res_root = etree.fromstring(zf.read(name))
        except Exception:
            continue
        for el in res_root.iter():
            if el.tag.rsplit("}", 1)[-1] != "DrawParam":
                continue
            fill = el.get("FillColor")
            if not fill:
                child = next(
                    (c for c in el.iter() if c.tag.rsplit("}", 1)[-1] == "FillColor"), None
                )
                if child is not None:
                    fill = child.get("Value")
            draw_params[el.get("ID")] = _parse_color(fill)

    # 可填充路径（字形轮廓）
    paths: list[tuple[tuple, str]] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] != "PathObject":
            continue
        if el.get("Fill") != "true":
            continue
        data_el = next((c for c in el.iter() if c.tag.rsplit("}", 1)[-1] == "AbbreviatedData"), None)
        if data_el is None or not data_el.text:
            continue
        color = draw_params.get(el.get("DrawParam"), (0.0, 0.0, 0.0))
        paths.append((color, data_el.text))

    if not paths:
        return None

    width_px = max(int(width_mm * px_per_mm), 1)
    height_px = max(int(height_mm * px_per_mm), 1)
    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width_px, height_px)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(1.0, 1.0, 1.0)
    ctx.paint()  # 白底
    ctx.scale(px_per_mm, px_per_mm)
    for color, data in paths:
        ctx.set_source_rgb(*color)
        _trace_path(ctx, data)
        ctx.fill()

    buf = io.BytesIO()
    surface.write_to_png(buf)
    return buf.getvalue()
