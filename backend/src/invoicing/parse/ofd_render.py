"""字体转曲 OFD 的矢量渲染：解析 PathObject/AbbreviatedData 贝塞尔路径 → Pillow 画整页 PNG。

背景：部分 OFD（如高德打车票）把文字画成字形轮廓路径，无 TextCode 文本层、
无整页图片——文本规则与图片 OCR 都失效。本模块把整页路径按 DrawParam 填充色
渲染为 PNG，供 OCR 识别。

渲染后端：**Pillow**（项目基础依赖，无系统库要求）。
历史：早先用 cairocffi，需系统 libcairo（macOS `brew install cairo`），在离线部署与
CI 中常缺失，导致渲染静默返回 None、OFD 预览 422（2 个测试长期失败）。改为 Pillow 后
无外部依赖；贝塞尔（Q/B）按固定段数采样为折线，对 OCR（6 px/mm ≈ 152 DPI）精度足够。

填充规则：**even-odd**（同色子路径用 XOR 合成）——字形轮廓的外框与镂空（如 'o' 的中空）
方向相反，仅用 polygon 逐条填充会把镂空涂实，影响 OCR 识别。
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


def _path_subpaths(data: str, curve_segments: int = 24) -> list[list[tuple[float, float]]]:
    """AbbreviatedData → 展平的多边形子路径列表（贝塞尔采样为折线）。

    OFD 路径算子（绝对坐标）：M 移动 / L 直线 / Q 二次贝塞尔(控制点,终点) /
    B 三次贝塞尔(控制点1,控制点2,终点) / C 闭合。
    """
    subpaths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    start: tuple[float, float] | None = None
    pos: tuple[float, float] = (0.0, 0.0)

    def flush():
        nonlocal current
        if len(current) >= 3:
            subpaths.append(current)
        current = []

    def quad(p0, p1, p2):
        for i in range(1, curve_segments + 1):
            t = i / curve_segments
            x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0]
            y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]
            current.append((x, y))

    def cubic(p0, p1, p2, p3):
        for i in range(1, curve_segments + 1):
            t = i / curve_segments
            mt = 1 - t
            x = mt**3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t**3 * p3[0]
            y = mt**3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t**3 * p3[1]
            current.append((x, y))

    for m in _TOKEN_RE.finditer(data):
        op = m.group(1)
        nums = _numbers(m.group(2))
        if op == "M":
            flush()
            if len(nums) >= 2:
                pos = (nums[0], nums[1])
                start = pos
                current = [pos]
            rest = nums[2:]
            i = 0
            while i + 1 < len(rest):  # 额外点对视为隐式 lineto（SVG 语义）
                pos = (rest[i], rest[i + 1])
                current.append(pos)
                i += 2
        elif op == "L":
            i = 0
            while i + 1 < len(nums):
                pos = (nums[i], nums[i + 1])
                current.append(pos)
                i += 2
        elif op == "Q":
            i = 0
            while i + 3 < len(nums):
                ctrl, end = (nums[i], nums[i + 1]), (nums[i + 2], nums[i + 3])
                quad(pos, ctrl, end)
                pos = end
                i += 4
        elif op == "B":
            i = 0
            while i + 5 < len(nums):
                c1, c2, end = (nums[i], nums[i + 1]), (nums[i + 2], nums[i + 3]), (nums[i + 4], nums[i + 5])
                cubic(pos, c1, c2, end)
                pos = end
                i += 6
        elif op == "C":
            if start is not None and current:
                current.append(start)
            flush()
    flush()
    return subpaths


def _fill_paths(img, paths: list[tuple[tuple, str]], px_per_mm: float) -> None:
    """按 even-odd 规则填充路径（同色子路径 XOR 合成，保留字形镂空）。

    正确的两步：① 在同一**路径级掩码**上累加各子路径（XOR = even-odd）
    ② 掩码为 1 处一次性贴色。若把 XOR 直接作用在图像内容上会反相
    （白色区域被当作"已填充"），这是首版实现的真实 bug。
    """
    from PIL import Image, ImageChops, ImageDraw

    for color, data in paths:
        subpaths = _path_subpaths(data)
        polys = [
            [(x * px_per_mm, y * px_per_mm) for x, y in poly]
            for poly in subpaths if len(poly) >= 3
        ]
        if not polys:
            continue
        xs = [p[0] for poly in polys for p in poly]
        ys = [p[1] for poly in polys for p in poly]
        x0, y0 = int(min(xs)), int(min(ys))
        x1, y1 = int(max(xs)) + 2, int(max(ys)) + 2
        if x1 <= x0 or y1 <= y0:
            continue
        size = (x1 - x0, y1 - y0)
        path_mask = Image.new("1", size, 0)
        for poly in polys:
            poly_mask = Image.new("1", size, 0)
            ImageDraw.Draw(poly_mask).polygon([(px - x0, py - y0) for px, py in poly], fill=1)
            path_mask = ImageChops.logical_xor(path_mask, poly_mask)
        rgb = tuple(int(round(c * 255)) for c in color)
        img.paste(Image.new("RGB", size, rgb), (x0, y0), path_mask)


def render_ofd_page_to_png(ofd_bytes: bytes, px_per_mm: float = 6.0) -> bytes | None:
    """把 OFD 首/唯一页的填充路径渲染为 PNG；无路径/非 OFD → None（路由落待复核）。"""
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

    from PIL import Image

    width_px = max(int(width_mm * px_per_mm), 1)
    height_px = max(int(height_mm * px_per_mm), 1)
    img = Image.new("RGB", (width_px, height_px), "white")
    _fill_paths(img, paths, px_per_mm)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
