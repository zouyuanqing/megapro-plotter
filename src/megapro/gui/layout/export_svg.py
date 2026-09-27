"""排版 → 单 SVG 导出（纯逻辑）。

事实契约（阶段 3，docs/preview-layout-blueprint.md §2.2）：输出 SVG 带
``width="210mm" height="210mm" viewBox="0 0 210 210"`` 声明（画板 = 整床），
几何数值为 **SVG y-down 用户单位 mm**（经 :func:`coords.paper_to_svg_ydown`
对合编码 ``y_svg = bed_h − y_paper``，浏览器所见 = 从上方看床、y 向下）。
页面/纸面 (0,0) = 工件原点（床左下）；x 不变。

只含 ``<polyline>``（无 ``<text>``/``<image>``/样式属性）；按 z 层序
（小→大，先画在下层）拍平、跳过 ``visible=False`` 与 <2 点折线 —— 与
toolchain ``parse_svg`` 互为交换格式。文本/图片已在入文档前转成折线，
这里只做「折线集合 → SVG」。
"""

from __future__ import annotations

__all__ = ["document_to_svg"]

from megapro.gui.canvas.coords import BED_H, BED_W, paper_to_svg_ydown
from megapro.gui.layout.model import Document, flatten_visible


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def document_to_svg(doc: Document, *, bed_h: float = BED_H) -> str:
    """把文档导出为单 SVG 字符串（SVG y-down mm，含 width/height/viewBox）。

    按 z 层序（小→大，先画在下层）；跳过 hidden/<2 点折线。y 数值经
    ``paper_to_svg_ydown`` 编码为 SVG y-down（对合，``bed_h`` 默认整床高）。
    """
    paths_svg = paper_to_svg_ydown(flatten_visible(doc), bed_h)
    parts: list[str] = []
    for p in paths_svg:
        pts = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in p)
        parts.append(f'<polyline points="{pts}" fill="none"/>')
    body = "\n  ".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_fmt(BED_W)}mm" height="{_fmt(bed_h)}mm" '
        f'viewBox="0 0 {_fmt(BED_W)} {_fmt(bed_h)}">\n  {body}\n</svg>\n'
    )
