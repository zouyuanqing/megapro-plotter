"""排版 → 单 SVG 导出（纯逻辑）。

契约（与 toolchain parse_svg 一致）：输出 SVG 的几何已是**绝对 mm 工件坐标**
（页面 (0,0)=工件原点），只含 path/polyline，无 <text>/<image>/样式。
y 向下（与机器一致）。调用方把它当普通 SVG 载入作业流水线即可。

文本/图片已在入文档前转成折线，这里只做「折线集合 → SVG」。
"""

from __future__ import annotations

__all__ = ["document_to_svg"]

from .model import Document


def _fmt(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def document_to_svg(doc: Document) -> str:
    """把文档导出为单 SVG 字符串（绝对 mm 工件坐标）。

    按 z 层序（小→大，先画在下层）；跳过 visible=False 的图元。
    """
    parts: list[str] = []
    for it in doc.items_visible():
        for p in it.transformed_paths():
            if len(p) < 2:
                continue
            pts = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in p)
            parts.append(f'<polyline points="{pts}" fill="none"/>')
    body = "\n  ".join(parts)
    return f'<svg xmlns="http://www.w3.org/2000/svg">\n  {body}\n</svg>\n'
